"""The owner's trusted policy: an action catalog and a contract.

The catalog says which actions exist and which artifact inputs and outputs each
declares. The contract says which actions a plan must contain, which must come
before which, and which inputs must refer to one planned artifact. Both are
validated together, before any candidate plan is looked at, and a policy that
could never be satisfied - a prerequisite cycle, an unknown action, an
artifact rule over incompatible types - is refused up front.

Every rule in a contract is required. A rule kind this engine does not
implement is reported as ``unsupported`` rather than skipped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .diagnostics import Diagnostic, Document, Location
from .loading import Loaded, Source, digest, load

CATALOG_SCHEMA = "planlens-agent/catalog/v1"
CONTRACT_SCHEMA = "planlens-agent/contract/v1"
SUPPORTED_RULE_KINDS = ("prerequisite", "same_artifact")

MAX_ACTIONS = 500
MAX_RULES = 500
MAX_SLOTS = 32
MAX_TEXT = 2000

ACTION_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SLOT = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ARTIFACT_TYPE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
NAME_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
RULE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$")


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
class PolicyIntegrityError(ValueError):
    """A policy object no longer matches the content it was validated from."""


def _owned(mapping: Mapping[str, Any]) -> Mapping[str, Any]:
    """A private, read-only copy. Callers cannot reach the underlying dict."""
    return MappingProxyType(dict(mapping))


@dataclass(frozen=True)
class ActionDef:
    id: str
    description: str
    inputs: Mapping[str, str]  # input name -> accepted artifact type
    outputs: Mapping[str, str]  # output name -> produced artifact type

    def __post_init__(self) -> None:
        # Owned read-only copies, also when built through dataclasses.replace().
        object.__setattr__(self, "inputs", _owned(self.inputs))
        object.__setattr__(self, "outputs", _owned(self.outputs))


@dataclass(frozen=True)
class Catalog:
    """A validated catalog. Every field is read-only all the way down.

    ``digest`` identifies the source document. ``binding`` ties that digest to
    the parsed model; :meth:`verify` recomputes it, so a catalog altered after
    validation - for example with ``dataclasses.replace`` - is detected rather
    than enforced under the old identity.
    """

    id: str
    version: str
    actions: Mapping[str, ActionDef]
    digest: str
    origin: str
    binding: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "actions", _owned(self.actions))

    def model(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "actions": {
                name: {
                    "description": a.description,
                    "inputs": dict(a.inputs),
                    "outputs": dict(a.outputs),
                }
                for name, a in sorted(self.actions.items())
            },
        }

    def verify(self) -> bool:
        return self.binding == _bind(self.digest, self.model())


@dataclass(frozen=True)
class Prerequisite:
    id: str
    index: int
    action: str
    requires_prior: str
    because: str


@dataclass(frozen=True)
class SameArtifact:
    id: str
    index: int
    inputs: tuple[tuple[str, str], ...]  # (action, input name)
    because: str


@dataclass(frozen=True)
class UnsupportedRule:
    id: str
    index: int
    kind: str


@dataclass(frozen=True)
class Contract:
    """A validated contract. Tuples and frozen records only; see :class:`Catalog`."""

    id: str
    version: str
    catalog_id: str
    catalog_version: str
    required_actions: tuple[str, ...]
    prerequisites: tuple[Prerequisite, ...]
    same_artifact: tuple[SameArtifact, ...]
    unsupported: tuple[UnsupportedRule, ...]
    digest: str
    origin: str
    binding: str

    def model(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "catalog": [self.catalog_id, self.catalog_version],
            "required_actions": list(self.required_actions),
            "prerequisites": [
                [p.id, p.index, p.action, p.requires_prior, p.because] for p in self.prerequisites
            ],
            "same_artifact": [
                [r.id, r.index, [list(i) for i in r.inputs], r.because] for r in self.same_artifact
            ],
            "unsupported": [[u.id, u.index, u.kind] for u in self.unsupported],
        }

    def verify(self) -> bool:
        return self.binding == _bind(self.digest, self.model())


def _bind(document_digest: str, model: dict[str, Any]) -> str:
    return digest({"document_sha256": document_digest, "model": model})


@dataclass(frozen=True)
class Policy:
    """A validated catalog/contract pair, or the reasons it is not usable.

    ``usable`` means the plan can be evaluated. A contract containing an
    unsupported rule kind is still usable for its supported rules, but no
    plan checked under it can pass.
    """

    catalog: Catalog | None
    contract: Contract | None
    diagnostics: tuple[Diagnostic, ...]
    # Where each document came from, known even when it could not be parsed, so a
    # syntax error can still be located in its file.
    catalog_origin: str | None = None
    contract_origin: str | None = None
    # Absolute paths fixed at load time; see ``Loaded.source``.
    catalog_source: str | None = field(default=None, repr=False)
    contract_source: str | None = field(default=None, repr=False)

    def verify(self) -> bool:
        """True when the catalog and contract still match what was validated."""
        return all(x.verify() for x in (self.catalog, self.contract) if x is not None)

    @property
    def usable(self) -> bool:
        return (
            self.catalog is not None
            and self.contract is not None
            and not any(d.category.startswith("invalid") for d in self.diagnostics)
        )


# ---------------------------------------------------------------------------
# Small schema helpers. ``out`` collects diagnostics; helpers return a value
# or None, and never raise on bad input.
# ---------------------------------------------------------------------------
class _Schema:
    def __init__(self, document: Document) -> None:
        self.document = document
        self.out: list[Diagnostic] = []

    def err(self, path: str, message: str, rule: str = "schema", **kw: Any) -> None:
        self.out.append(
            Diagnostic(f"{self.document}/{rule}", message, Location(self.document, path), **kw)
        )

    def keys(
        self, obj: dict[str, Any], path: str, required: Iterable[str], optional: Iterable[str]
    ) -> bool:
        required = tuple(required)
        allowed = set(required) | set(optional)
        ok = True
        for key in required:
            if key not in obj:
                self.err(
                    f"{path}/{key}",
                    f"missing required field {key!r}",
                    required=key,
                    suggestion=f"Add the {key!r} field.",
                )
                ok = False
        for key in sorted(obj):
            if key not in allowed:
                self.err(
                    f"{path}/{key}",
                    f"unknown field {key!r}",
                    observed=key,
                    required=sorted(allowed),
                    suggestion="Remove it, or check the spelling against the schema. "
                    "Unknown fields are not ignored.",
                )
                ok = False
        return ok

    def string(
        self,
        obj: dict[str, Any],
        key: str,
        path: str,
        pattern: re.Pattern[str] | None = None,
        max_len: int = 64,
    ) -> str | None:
        value = obj.get(key)
        p = f"{path}/{key}"
        if not isinstance(value, str):
            self.err(p, f"{key!r} must be a string", observed=type(value).__name__)
            return None
        if len(value) > max_len:
            self.err(
                p, f"{key!r} is longer than {max_len} characters", rule="limit", observed=len(value)
            )
            return None
        if pattern is not None and not pattern.match(value):
            self.err(
                p,
                f"{key!r} value {value!r} does not match {pattern.pattern}",
                observed=value,
                required=pattern.pattern,
            )
            return None
        return value

    def text(self, obj: dict[str, Any], key: str, path: str) -> str:
        if key not in obj:
            return ""
        return self.string(obj, key, path, max_len=MAX_TEXT) or ""

    def schema_id(self, obj: dict[str, Any], expected: str) -> bool:
        value = obj.get("schema")
        if not isinstance(value, str):
            self.err(
                "/schema",
                f"missing or non-string 'schema'; expected {expected!r}",
                required=expected,
            )
            return False
        if value == expected:
            return True
        family = expected.rsplit("/", 1)[0] + "/"
        if value.startswith(family):
            self.err(
                "/schema",
                f"schema version {value!r} is not supported; this engine implements {expected!r}",
                rule="unsupported-version",
                observed=value,
                required=expected,
            )
        else:
            self.err(
                "/schema",
                f"'schema' is {value!r}; expected {expected!r}",
                observed=value,
                required=expected,
            )
        return False


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
def _parse_catalog(loaded: Loaded) -> tuple[Catalog | None, list[Diagnostic]]:
    if loaded.data is None:
        return None, list(loaded.diagnostics)
    data, s = loaded.data, _Schema("catalog")
    if not s.schema_id(data, CATALOG_SCHEMA):
        return None, s.out
    s.keys(data, "", ("schema", "id", "version", "actions"), ("description",))
    cid = s.string(data, "id", "", NAME_ID) if "id" in data else None
    ver = s.string(data, "version", "", VERSION) if "version" in data else None
    s.text(data, "description", "")
    actions: dict[str, ActionDef] = {}
    raw = data.get("actions")
    if "actions" in data and not isinstance(raw, dict):
        s.err("/actions", "'actions' must be a table of action definitions")
    elif isinstance(raw, dict):
        if not raw:
            s.err("/actions", "the catalog defines no actions")
        if len(raw) > MAX_ACTIONS:
            s.err("/actions", f"{len(raw)} actions; the limit is {MAX_ACTIONS}", rule="limit")
            raw = {}
        for name in sorted(raw):
            path = f"/actions/{name}"
            if not ACTION_ID.match(name):
                s.err(
                    path,
                    f"action id {name!r} does not match {ACTION_ID.pattern}",
                    observed=name,
                    required=ACTION_ID.pattern,
                )
                continue
            body = raw[name]
            if not isinstance(body, dict):
                s.err(path, f"action {name!r} must be a table")
                continue
            s.keys(body, path, (), ("description", "inputs", "outputs"))
            ins = _slots(s, body, "inputs", path)
            outs = _slots(s, body, "outputs", path)
            actions[name] = ActionDef(name, s.text(body, "description", path), ins, outs)
    if s.out or cid is None or ver is None:
        return None, s.out
    doc = digest(data)
    model = Catalog(cid, ver, actions, doc, loaded.origin, "").model()
    return Catalog(cid, ver, actions, doc, loaded.origin, _bind(doc, model)), []


def _slots(s: _Schema, body: dict[str, Any], key: str, path: str) -> dict[str, str]:
    raw = body.get(key, {})
    p = f"{path}/{key}"
    if not isinstance(raw, dict):
        s.err(p, f"{key!r} must map names to artifact types")
        return {}
    if len(raw) > MAX_SLOTS:
        s.err(p, f"{len(raw)} {key}; the limit is {MAX_SLOTS}", rule="limit")
        return {}
    out: dict[str, str] = {}
    for name in sorted(raw):
        if not SLOT.match(name):
            s.err(f"{p}/{name}", f"name {name!r} does not match {SLOT.pattern}", observed=name)
            continue
        t = raw[name]
        if not isinstance(t, str) or not ARTIFACT_TYPE.match(t):
            s.err(
                f"{p}/{name}",
                f"artifact type for {name!r} must match {ARTIFACT_TYPE.pattern}",
                observed=t,
            )
            continue
        out[name] = t
    return out


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------
def _parse_contract(loaded: Loaded) -> tuple[Contract | None, list[Diagnostic]]:
    if loaded.data is None:
        return None, list(loaded.diagnostics)
    data, s = loaded.data, _Schema("contract")
    if not s.schema_id(data, CONTRACT_SCHEMA):
        return None, s.out
    s.keys(
        data,
        "",
        ("schema", "id", "version", "catalog"),
        ("description", "required_actions", "rules"),
    )
    cid = s.string(data, "id", "", NAME_ID) if "id" in data else None
    ver = s.string(data, "version", "", VERSION) if "version" in data else None
    s.text(data, "description", "")

    pin_id = pin_ver = None
    pin = data.get("catalog")
    if "catalog" in data:
        if isinstance(pin, dict):
            s.keys(pin, "/catalog", ("id", "version"), ())
            pin_id = s.string(pin, "id", "/catalog", NAME_ID) if "id" in pin else None
            pin_ver = s.string(pin, "version", "/catalog", VERSION) if "version" in pin else None
        else:
            s.err("/catalog", "'catalog' must be a table with 'id' and 'version'")

    required: list[str] = []
    raw_req = data.get("required_actions", [])
    if not isinstance(raw_req, list):
        s.err("/required_actions", "'required_actions' must be a list of action ids")
    else:
        for i, a in enumerate(raw_req):
            if not isinstance(a, str) or not ACTION_ID.match(a):
                s.err(f"/required_actions/{i}", f"{a!r} is not a valid action id", observed=a)
            elif a in required:
                s.err(f"/required_actions/{i}", f"action {a!r} is listed twice", observed=a)
            else:
                required.append(a)

    prereqs: list[Prerequisite] = []
    same: list[SameArtifact] = []
    unsupported: list[UnsupportedRule] = []
    raw_rules = data.get("rules", [])
    seen_ids: set[str] = set()
    if not isinstance(raw_rules, list):
        s.err("/rules", "'rules' must be a list of rule tables")
        raw_rules = []
    if len(raw_rules) > MAX_RULES:
        s.err("/rules", f"{len(raw_rules)} rules; the limit is {MAX_RULES}", rule="limit")
        raw_rules = []
    for i, rule in enumerate(raw_rules):
        path = f"/rules/{i}"
        if not isinstance(rule, dict):
            s.err(path, "each rule must be a table")
            continue
        rid = s.string(rule, "id", path, RULE_ID) if "id" in rule else None
        if rid is None and "id" not in rule:
            s.err(f"{path}/id", "rule is missing 'id'", suggestion="Give every rule a short id.")
        kind = rule.get("kind")
        if not isinstance(kind, str):
            s.err(
                f"{path}/kind",
                "rule is missing a string 'kind'",
                required=list(SUPPORTED_RULE_KINDS),
            )
            continue
        if rid is not None:
            if rid in seen_ids:
                s.err(f"{path}/id", f"rule id {rid!r} is used twice", observed=rid)
            seen_ids.add(rid)
        label = rid or f"rules[{i}]"
        if kind == "prerequisite":
            s.keys(rule, path, ("id", "kind", "action", "requires_prior"), ("because",))
            act = s.string(rule, "action", path, ACTION_ID) if "action" in rule else None
            pre = (
                s.string(rule, "requires_prior", path, ACTION_ID)
                if "requires_prior" in rule
                else None
            )
            because = s.text(rule, "because", path)
            if act and pre:
                prereqs.append(Prerequisite(label, i, act, pre, because))
        elif kind == "same_artifact":
            s.keys(rule, path, ("id", "kind", "inputs"), ("because",))
            refs = _artifact_refs(s, rule, path)
            because = s.text(rule, "because", path)
            if refs is not None:
                same.append(SameArtifact(label, i, refs, because))
        else:
            # Do not validate fields of a kind we cannot interpret. Reported
            # as unsupported in the cross-check so it is never skipped.
            unsupported.append(UnsupportedRule(label, i, kind))

    if s.out or cid is None or ver is None or pin_id is None or pin_ver is None:
        return None, s.out
    fields = (
        cid,
        ver,
        pin_id,
        pin_ver,
        tuple(required),
        tuple(prereqs),
        tuple(same),
        tuple(unsupported),
        digest(data),
        loaded.origin,
    )
    model = Contract(*fields, "").model()
    return Contract(*fields, _bind(fields[8], model)), []


def _artifact_refs(
    s: _Schema, rule: dict[str, Any], path: str
) -> tuple[tuple[str, str], ...] | None:
    raw = rule.get("inputs")
    if not isinstance(raw, list):
        s.err(f"{path}/inputs", "'inputs' must be a list of \"action.input\" strings")
        return None
    out: list[tuple[str, str]] = []
    for j, ref in enumerate(raw):
        p = f"{path}/inputs/{j}"
        if not isinstance(ref, str) or ref.count(".") != 1:
            s.err(p, f'{ref!r} must have the form "action.input"', observed=ref)
            return None
        action, slot = ref.split(".")
        if not ACTION_ID.match(action) or not SLOT.match(slot):
            s.err(p, f'{ref!r} must have the form "action.input"', observed=ref)
            return None
        if (action, slot) in out:
            s.err(p, f"{ref!r} is listed twice", observed=ref)
            return None
        out.append((action, slot))
    if len(out) < 2:
        s.err(f"{path}/inputs", "a same_artifact rule needs at least two inputs to compare")
        return None
    return tuple(out)


# ---------------------------------------------------------------------------
# Cross-checks between contract and catalog
# ---------------------------------------------------------------------------
def _c(path: str) -> Location:
    return Location("contract", path)


def _cross_check(contract: Contract, catalog: Catalog) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    acts = catalog.actions

    if (contract.catalog_id, contract.catalog_version) != (catalog.id, catalog.version):
        out.append(
            Diagnostic(
                "contract/catalog-mismatch",
                f"contract expects catalog {contract.catalog_id} {contract.catalog_version}, "
                f"got {catalog.id} {catalog.version}",
                _c("/catalog"),
                observed={"id": catalog.id, "version": catalog.version},
                required={"id": contract.catalog_id, "version": contract.catalog_version},
                suggestion="Supply the catalog this contract was written for, or update the "
                "contract's catalog pin after reviewing the rules against the new catalog.",
            )
        )

    def unknown(action: str, path: str, rule: str | None) -> None:
        out.append(
            Diagnostic(
                "contract/unknown-action",
                f"action {action!r} is not in catalog {catalog.id}",
                _c(path),
                observed=action,
                required=sorted(acts),
                contract_rule=rule,
                suggestion="Add the action to the catalog, or correct the name in the contract.",
            )
        )

    for i, a in enumerate(contract.required_actions):
        if a not in acts:
            unknown(a, f"/required_actions/{i}", None)

    for p in contract.prerequisites:
        for field_name, a in (("action", p.action), ("requires_prior", p.requires_prior)):
            if a not in acts:
                unknown(a, f"/rules/{p.index}/{field_name}", p.id)

    for r in contract.same_artifact:
        types: dict[tuple[str, str], str] = {}
        for j, (a, slot) in enumerate(r.inputs):
            path = f"/rules/{r.index}/inputs/{j}"
            if a not in acts:
                unknown(a, path, r.id)
            elif slot not in acts[a].inputs:
                out.append(
                    Diagnostic(
                        "contract/unknown-action",
                        f"action {a!r} has no input {slot!r}",
                        _c(path),
                        observed=f"{a}.{slot}",
                        required=sorted(acts[a].inputs),
                        contract_rule=r.id,
                        suggestion="Name an input the catalog declares for this action.",
                    )
                )
            else:
                types[(a, slot)] = acts[a].inputs[slot]
        if len(set(types.values())) > 1:
            out.append(
                Diagnostic(
                    "contract/inconsistent-artifact-rule",
                    f"rule {r.id!r} compares inputs of different artifact types, so no single "
                    "artifact can satisfy it",
                    _c(f"/rules/{r.index}/inputs"),
                    observed={f"{a}.{s}": t for (a, s), t in types.items()},
                    required="one artifact type across all listed inputs",
                    contract_rule=r.id,
                    suggestion="List only inputs that accept the same artifact type.",
                )
            )

    out.extend(_cycles(contract))

    runnable, available = bootstrap(acts)
    for i, a in enumerate(contract.required_actions):
        if a not in acts or a in runnable:
            continue
        blocked = {slot: t for slot, t in sorted(acts[a].inputs.items()) if t not in available}
        producers = {
            t: sorted(n for n, d in acts.items() if t in d.outputs.values())
            for t in sorted(set(blocked.values()))
        }
        reasons = []
        for t, names in producers.items():
            if names:
                reasons.append(
                    f"{t!r} is produced only by {', '.join(names)}, which can never "
                    "run first because its own inputs cannot be produced earlier"
                )
            else:
                reasons.append(f"no catalog action produces {t!r}")
        out.append(
            Diagnostic(
                "contract/unsatisfiable-input",
                f"required action {a!r} can never run: " + "; ".join(reasons),
                _c(f"/required_actions/{i}"),
                related=tuple(
                    Location("catalog", f"/actions/{a}/inputs/{slot}") for slot in blocked
                ),
                observed={"unavailable_inputs": blocked, "producers": producers},
                required="every input type produced by an action that can run before it",
                evidence={
                    "runnable_actions": sorted(runnable),
                    "producible_types": sorted(available),
                },
                suggestion=(
                    "Add an action that produces the missing type from inputs that are "
                    "available, or stop requiring this action."
                ),
            )
        )

    for u in contract.unsupported:
        out.append(
            Diagnostic(
                "contract/unsupported-rule",
                f"rule {u.id!r} has kind {u.kind!r}, which this engine does not implement",
                _c(f"/rules/{u.index}"),
                observed=u.kind,
                required=list(SUPPORTED_RULE_KINDS),
                contract_rule=u.id,
                suggestion="Remove the rule only if the requirement is genuinely not needed. "
                "Otherwise this contract cannot be enforced by this engine, and plans "
                "checked under it will not pass.",
            )
        )
    return out


def bootstrap(actions: Mapping[str, ActionDef]) -> tuple[set[str], set[str]]:
    """Which actions can run in some finite sequential plan, and which artifact types
    such a plan can produce.

    Every declared input is required, and a step may only consume outputs of earlier
    steps. So an action can run once every input type it needs is produced by some
    action that can already run; actions without inputs can run first. This is the
    least fixpoint of that rule. A type cycle is satisfiable when any action on it is
    reachable from such a start; it is unsatisfiable only when none is.

    Prerequisite rules are not considered here: the result can only over-estimate
    what is runnable, so it never rejects a satisfiable contract.
    """
    runnable: set[str] = set()
    available: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name in sorted(actions):
            if name not in runnable and set(actions[name].inputs.values()) <= available:
                runnable.add(name)
                available |= set(actions[name].outputs.values())
                changed = True
    return runnable, available


def _cycles(contract: Contract) -> list[Diagnostic]:
    """Strongly connected components of the prerequisite graph (Tarjan)."""
    edges: dict[str, list[str]] = {}
    for p in contract.prerequisites:
        edges.setdefault(p.requires_prior, []).append(p.action)
        edges.setdefault(p.action, [])
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    comps: list[list[str]] = []
    counter = 0

    def visit(v: str) -> None:
        nonlocal counter
        index[v] = low[v] = counter
        counter += 1
        stack.append(v)
        on.add(v)
        for w in sorted(edges[v]):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            comps.append(sorted(comp))

    for v in sorted(edges):
        if v not in index:
            visit(v)

    out: list[Diagnostic] = []
    for comp in sorted(comps):
        members = set(comp)
        involved = [
            p for p in contract.prerequisites if p.action in members and p.requires_prior in members
        ]
        if len(comp) == 1 and not involved:
            continue
        out.append(
            Diagnostic(
                "contract/cycle",
                f"prerequisite rules form a cycle over {', '.join(comp)}",
                _c(f"/rules/{involved[0].index}"),
                related=tuple(_c(f"/rules/{p.index}") for p in involved[1:]),
                observed={"actions": comp, "rules": [p.id for p in involved]},
                required="prerequisites without cycles",
                contract_rule=involved[0].id,
                suggestion="Remove or reverse one of these prerequisites; as written, no plan "
                "containing these actions can satisfy them.",
            )
        )
    return out


def validate_policy(contract: Source, catalog: Source) -> Policy:
    """Load and validate the owner's contract and catalog together."""
    cat_doc, con_doc = load(catalog, "catalog"), load(contract, "contract")
    cat, cat_diags = _parse_catalog(cat_doc)
    con, con_diags = _parse_contract(con_doc)
    diags = cat_diags + con_diags
    if cat is not None and con is not None:
        diags += _cross_check(con, cat)
    return Policy(
        cat, con, tuple(diags), cat_doc.origin, con_doc.origin, cat_doc.source, con_doc.source
    )
