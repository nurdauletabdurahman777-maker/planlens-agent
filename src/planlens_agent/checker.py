"""Evaluating a candidate plan against a validated policy.

The verdict is decided by the most serious category present:

====================  =========  ====
category              verdict    exit
====================  =========  ====
invalid catalog,      error      2
contract or plan
unsupported           error      2
violation             reject     1
(none)                pass       0
====================  =========  ====

"pass" means the plan satisfies every *supported, required* check in the owner's
contract and catalog. It is a statement about the declared plan only.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from ._version import __version__
from .approval import ApprovedPlan, NotApproved
from .diagnostics import CATEGORY_ORDER, Diagnostic, Location
from .loading import Source, digest, freeze, load
from .plan import Plan, Step, parse_plan
from .policy import Catalog, Contract, Policy, PolicyIntegrityError, validate_policy

Verdict = Literal["pass", "reject", "error"]

REPORT_SCHEMA = "planlens-agent/report/v1"

SCOPE = (
    "declared-plan checks: the candidate plan was compared with the owner's contract "
    "and action catalog before execution"
)

NOT_CHECKED = (
    "whether any step actually runs, succeeds or does what its action name suggests",
    "whether tests passed, scans were clean or an approval was actually granted",
    "artifact contents or digests (only the planned producer step and output are compared)",
    "requirements that are not written in the owner's contract",
)


def _p(i: int, step: Step | None = None, suffix: str = "") -> Location:
    return Location("plan", f"/steps/{i}{suffix}", step_id=step.id if step else None, step_index=i)


def evaluate(plan: Plan, catalog: Catalog, contract: Contract) -> list[Diagnostic]:
    """Every violation of the catalog and contract by a well-formed plan."""
    out: list[Diagnostic] = []
    acts = catalog.actions
    by_id = {s.id: s for s in plan.steps}
    resolved: dict[tuple[str, str], tuple[str, str]] = {}

    # --- actions and artifact references -----------------------------------
    for s in plan.steps:
        if s.action not in acts:
            out.append(
                Diagnostic(
                    "plan/unknown-action",
                    f"steps[{s.index}] ({s.id}) uses action {s.action!r}, which catalog "
                    f"{catalog.id} does not define",
                    _p(s.index, s, "/action"),
                    observed=s.action,
                    required=sorted(acts),
                    suggestion="Use an action from the catalog. Only the owner can add actions.",
                )
            )
            continue
        adef = acts[s.action]
        for name in sorted(adef.inputs):
            if name not in s.inputs:
                out.append(
                    Diagnostic(
                        "plan/missing-input",
                        f"steps[{s.index}] ({s.id}, {s.action}) does not bind "
                        f"required input {name!r}",
                        _p(s.index, s, "/inputs"),
                        observed=sorted(s.inputs),
                        required={name: adef.inputs[name]},
                        suggestion=f'Add "inputs": {{"{name}": "<earlier_step_id>.<output>"}} '
                        f"referring to an output of type {adef.inputs[name]!r}.",
                    )
                )
        for name, ref in sorted(s.inputs.items()):
            path = f"/inputs/{name}"
            if name not in adef.inputs:
                out.append(
                    Diagnostic(
                        "plan/unknown-input",
                        f"steps[{s.index}] ({s.id}) binds input {name!r}, which action "
                        f"{s.action!r} does not declare",
                        _p(s.index, s, path),
                        observed=name,
                        required=sorted(adef.inputs),
                        suggestion="Remove the binding or use an input the action declares.",
                    )
                )
                continue
            pid, oslot = ref.split(".")
            prod = by_id.get(pid)
            if prod is None:
                out.append(
                    Diagnostic(
                        "plan/unresolved-reference",
                        f"steps[{s.index}] ({s.id}) input {name!r} refers to {ref!r}, but no step "
                        f"has id {pid!r}",
                        _p(s.index, s, path),
                        observed=ref,
                        required="an existing earlier step id",
                        evidence={"step_ids": [x.id for x in plan.steps]},
                        suggestion="Refer to the id of the step that produces this artifact.",
                    )
                )
                continue
            if prod.action not in acts or oslot not in acts[prod.action].outputs:
                outs = sorted(acts[prod.action].outputs) if prod.action in acts else []
                why = (
                    f"its action {prod.action!r} is unknown"
                    if prod.action not in acts
                    else f"its action {prod.action!r} declares outputs {outs or 'none'}"
                )
                out.append(
                    Diagnostic(
                        "plan/unresolved-reference",
                        f"steps[{s.index}] ({s.id}) input {name!r} refers to output {oslot!r} of "
                        f"step {pid!r}, but {why}",
                        _p(s.index, s, path),
                        observed=ref,
                        required=[f"{pid}.{o}" for o in outs] or "a declared output",
                        related=(_p(prod.index, prod),),
                        suggestion="Refer to an output the producing action declares.",
                    )
                )
                continue
            ptype, want = acts[prod.action].outputs[oslot], adef.inputs[name]
            if ptype != want:
                out.append(
                    Diagnostic(
                        "plan/artifact-type",
                        f"steps[{s.index}] ({s.id}) input {name!r} expects {want!r}, but {ref!r} "
                        f"produces {ptype!r}",
                        _p(s.index, s, path),
                        observed=ptype,
                        required=want,
                        related=(_p(prod.index, prod),),
                        suggestion=f"Refer to an output of type {want!r}.",
                    )
                )
            if prod.index >= s.index:
                where = (
                    "itself" if prod.index == s.index else f"steps[{prod.index}], which comes later"
                )
                out.append(
                    Diagnostic(
                        "plan/reference-before-producer",
                        f"steps[{s.index}] ({s.id}) consumes {ref!r}, produced by {where}",
                        _p(s.index, s, path),
                        observed={"consumer_index": s.index, "producer_index": prod.index},
                        required="producer before consumer",
                        related=(_p(prod.index, prod),),
                        suggestion=f"Move step {pid!r} before step {s.id!r}.",
                    )
                )
            resolved[(s.id, name)] = (pid, oslot)

    # --- required actions ----------------------------------------------------
    present = {s.action for s in plan.steps}
    for i, a in enumerate(contract.required_actions):
        if a not in present:
            out.append(
                Diagnostic(
                    "plan/missing-required-action",
                    f"the contract requires action {a!r}, and no step performs it",
                    None,
                    location_absent_reason="the required step does not exist in the plan",
                    related=(Location("contract", f"/required_actions/{i}"),),
                    observed=sorted(present),
                    required=a,
                    suggestion=f"Add a step with action {a!r} in a position that satisfies the "
                    "contract's prerequisites.",
                )
            )

    # --- prerequisites ---------------------------------------------------
    for rule in contract.prerequisites:
        prior = [x for x in plan.steps if x.action == rule.requires_prior]
        for s in plan.steps:
            if s.action != rule.action or any(x.index < s.index for x in prior):
                continue
            later = [x for x in prior if x.index > s.index]
            if later:
                seen = ", ".join(f"steps[{x.index}] ({x.id})" for x in later)
                msg = (
                    f"steps[{s.index}] ({s.id}) runs {rule.action!r} before any "
                    f"{rule.requires_prior!r}; {rule.requires_prior!r} appears later, at {seen}"
                )
                fix = (
                    f"Move step {later[0].id!r} before step {s.id!r}, or place a "
                    f"{rule.requires_prior!r} step earlier."
                )
            else:
                msg = (
                    f"steps[{s.index}] ({s.id}) runs {rule.action!r}, and the plan has no "
                    f"{rule.requires_prior!r} step before it or anywhere else"
                )
                fix = f"Add a {rule.requires_prior!r} step before step {s.id!r}."
            out.append(
                Diagnostic(
                    "plan/prerequisite-order",
                    msg,
                    _p(s.index, s),
                    observed={
                        "prior_occurrences": [],
                        "later_occurrences": [
                            {"step_id": x.id, "step_index": x.index} for x in later
                        ],
                    },
                    required=f"{rule.requires_prior} before {rule.action}",
                    contract_rule=rule.id,
                    related=tuple(_p(x.index, x) for x in later)
                    + (Location("contract", f"/rules/{rule.index}"),),
                    evidence={"because": rule.because} if rule.because else {},
                    suggestion=fix,
                )
            )

    # --- planned artifact identity -----------------------------------------
    for r in contract.same_artifact:
        bindings = [
            (s, name, resolved[(s.id, name)])
            for s in plan.steps
            for (a, name) in r.inputs
            if s.action == a and (s.id, name) in resolved
        ]
        if len({b[2] for b in bindings}) <= 1:
            continue
        first = bindings[0]
        odd = next(b for b in bindings if b[2] != first[2])
        out.append(
            Diagnostic(
                "plan/artifact-identity",
                f"steps[{odd[0].index}] ({odd[0].id}) input {odd[1]!r} refers to "
                f"{odd[2][0]}.{odd[2][1]}, but steps[{first[0].index}] ({first[0].id}) input "
                f"{first[1]!r} refers to {first[2][0]}.{first[2][1]}",
                _p(odd[0].index, odd[0], f"/inputs/{odd[1]}"),
                observed=[
                    {"step_id": s.id, "step_index": s.index, "input": n, "refers_to": f"{pid}.{o}"}
                    for s, n, (pid, o) in bindings
                ],
                required="one planned artifact across "
                + ", ".join(f"{a}.{n}" for a, n in r.inputs),
                contract_rule=r.id,
                related=tuple(
                    _p(s.index, s, f"/inputs/{n}") for s, n, _ in bindings if s is not odd[0]
                )
                + (Location("contract", f"/rules/{r.index}"),),
                evidence={"because": r.because} if r.because else {},
                suggestion="Make every listed input refer to the same producer output. The checker "
                "cannot tell which reference is the wrong one.",
            )
        )
    return out


def _sort_key(d: Diagnostic) -> tuple[int, int, str, str]:
    loc = d.location
    pos = loc.step_index if loc is not None and loc.step_index is not None else 1 << 30
    return (CATEGORY_ORDER.index(d.category), pos, d.rule_id, loc.path if loc else "")


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one check. Immutable; ``approve()`` derives a dispatchable snapshot."""

    verdict: Verdict
    error_kind: str | None
    diagnostics: tuple[Diagnostic, ...]
    plan_evaluated: bool
    plan_origin: str
    plan_digest: str | None
    step_count: int | None
    policy: Policy
    plan_snapshot: Mapping[str, Any] | None = field(default=None, repr=False)
    plan_source: str | None = field(default=None, repr=False)  # see ``Loaded.source``

    @property
    def exit_code(self) -> int:
        return {"pass": 0, "reject": 1, "error": 2}[self.verdict]

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"

    def approve(self) -> ApprovedPlan:
        """Bind the exact checked snapshot to the policy it passed under.

        Raises :class:`NotApproved` unless the verdict is ``pass``.
        """
        if (
            not self.passed
            or self.plan_snapshot is None
            or self.plan_digest is None
            or self.policy.contract is None
            or self.policy.catalog is None
        ):
            raise NotApproved(self.verdict, len(self.diagnostics))
        c, k = self.policy.contract, self.policy.catalog
        return ApprovedPlan(
            plan=self.plan_snapshot,
            plan_digest=self.plan_digest,
            contract_id=c.id,
            contract_version=c.version,
            contract_digest=c.digest,
            catalog_id=k.id,
            catalog_version=k.version,
            catalog_digest=k.digest,
            engine_version=__version__,
        )

    def to_dict(self) -> dict[str, Any]:
        from .report import report_dict

        return report_dict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=True) + "\n"

    def render_text(self) -> str:
        from .report import render_text

        return render_text(self)


def check(
    plan: Source,
    contract: Source | None = None,
    catalog: Source | None = None,
    *,
    policy: Policy | None = None,
) -> CheckResult:
    """Check a candidate plan against the owner's contract and catalog.

    ``plan``, ``contract`` and ``catalog`` may each be a path to a ``.json`` or
    ``.toml`` file, or a mapping. Pass ``policy=`` instead of contract and
    catalog to reuse a policy validated once with :func:`validate_policy`.

    Problems with any input are reported as diagnostics in the result; this
    function raises only for programming errors such as wrong argument types.
    """
    if policy is None:
        if contract is None or catalog is None:
            raise TypeError("check() needs contract= and catalog=, or policy=")
        policy = validate_policy(contract, catalog)
    elif contract is not None or catalog is not None:
        raise TypeError("pass either policy= or contract=/catalog=, not both")
    if not policy.verify():
        raise PolicyIntegrityError(
            "this policy no longer matches the catalog and contract it was validated "
            "from; validate the changed documents with validate_policy() to obtain a "
            "new policy with its own identity"
        )

    diags = list(policy.diagnostics)
    loaded = load(plan, "plan")  # a fresh copy: later caller mutations cannot reach it
    snapshot = freeze(loaded.data) if loaded.data is not None else None
    plan_digest = digest(loaded.data) if loaded.data is not None else None
    evaluated = False
    steps = None
    if policy.usable:
        parsed, plan_diags = parse_plan(loaded)
        diags += plan_diags
        if parsed is not None and policy.catalog is not None and policy.contract is not None:
            steps = len(parsed.steps)
            diags += evaluate(parsed, policy.catalog, policy.contract)
            evaluated = True

    diags.sort(key=_sort_key)
    cats = Counter(d.category for d in diags)
    verdict: Verdict
    error_kind = next((c for c in CATEGORY_ORDER[:4] if cats[c]), None)
    if error_kind is not None:
        verdict = "error"
    elif cats["violation"]:
        verdict = "reject"
    elif evaluated:
        verdict = "pass"
    else:  # nothing evaluated and nothing reported: never a silent pass
        verdict, error_kind = "error", "invalid_plan"
    return CheckResult(
        verdict,
        error_kind,
        tuple(diags),
        evaluated,
        loaded.origin,
        plan_digest,
        steps,
        policy,
        snapshot,
        plan_source=loaded.source,
    )
