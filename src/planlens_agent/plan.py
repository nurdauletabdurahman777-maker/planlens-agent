"""Parsing a candidate plan.

A plan is untrusted proposed data: an ordered list of steps, where list order
is the proposed execution order. Each step names a catalog action and binds
that action's inputs to outputs of earlier steps as ``"<step_id>.<output>"``.

Three kinds of field are distinguished, because they mean different things:

* fields of this schema version, which are read;
* fields that try to change trusted policy (waivers, a replacement contract,
  a step declaring itself harmless or approved). These are recognised, never
  honoured, and reported as ``plan/policy-override`` violations;
* fields asking for semantics this version does not implement (parallel
  groups, conditions, loops, candidate-declared dependencies). The plan is
  refused as ``plan/unsupported-semantics`` rather than read as if they were
  absent.

Any other field is a schema error. Nothing is silently ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .diagnostics import Diagnostic, Location
from .loading import Loaded

PLAN_SCHEMA = "planlens-agent/plan/v1"
MAX_STEPS = 1000
MAX_INPUTS = 32
MAX_TEXT = 2000

STEP_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
ACTION_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
INPUT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
REFERENCE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]{0,63})\.([a-z][a-z0-9_]{0,63})$")

PLAN_FIELDS = {"schema", "steps", "title", "description", "mode"}
STEP_FIELDS = {"id", "action", "inputs", "description"}

#: Fields a candidate might use to weaken or replace the owner's policy.
POLICY_FIELDS_PLAN = {
    "contract",
    "catalog",
    "policy",
    "rules",
    "required_actions",
    "waive",
    "waivers",
    "skip_rules",
    "disable_rules",
    "overrides",
    "contract_version",
    "catalog_version",
    "policy_version",
}
POLICY_FIELDS_STEP = {
    "waive",
    "waivers",
    "skip_checks",
    "skip_rules",
    "disable_rules",
    "harmless",
    "safe",
    "trusted",
    "capabilities",
    "effects",
    "outputs",
    "policy",
    "contract",
    "approved",
    "approval_granted",
}

#: Fields asking for execution semantics outside ordered sequential plans.
UNSUPPORTED_PLAN = {"edges", "dependencies", "parallel", "graph"}
UNSUPPORTED_STEP = {
    "depends_on",
    "after",
    "before",
    "needs",
    "parallel",
    "parallel_group",
    "when",
    "if",
    "condition",
    "loop",
    "repeat",
    "for_each",
    "retry",
    "branches",
    "on_failure",
}


@dataclass(frozen=True)
class Step:
    index: int
    id: str
    action: str
    inputs: dict[str, str]
    description: str


@dataclass(frozen=True)
class Plan:
    steps: tuple[Step, ...]
    origin: str


def _loc(path: str, step: tuple[str | None, int | None] = (None, None)) -> Location:
    return Location("plan", path, step_id=step[0], step_index=step[1])


def parse_plan(loaded: Loaded) -> tuple[Plan | None, list[Diagnostic]]:
    """Return the parsed plan (or None) and every diagnostic found while reading it.

    The plan is returned only when it has no input errors and no unsupported
    semantics. Policy-override violations do not prevent parsing: they are
    reported, and the plan is still evaluated against every requirement.
    """
    if loaded.data is None:
        return None, list(loaded.diagnostics)
    data = loaded.data
    out: list[Diagnostic] = []

    def schema(path: str, msg: str, **kw: Any) -> None:
        out.append(Diagnostic("plan/schema", msg, _loc(path), **kw))

    value = data.get("schema")
    if value != PLAN_SCHEMA:
        if isinstance(value, str) and value.startswith("planlens-agent/plan/"):
            out.append(
                Diagnostic(
                    "plan/unsupported-version",
                    f"plan schema version {value!r} is not supported; this engine implements "
                    f"{PLAN_SCHEMA!r}",
                    _loc("/schema"),
                    observed=value,
                    required=PLAN_SCHEMA,
                )
            )
        else:
            schema(
                "/schema",
                f"'schema' must be {PLAN_SCHEMA!r}",
                observed=value,
                required=PLAN_SCHEMA,
                suggestion=f'Add "schema": "{PLAN_SCHEMA}" at the top level of the plan.',
            )
        return None, out

    for key in sorted(data):
        if key in POLICY_FIELDS_PLAN:
            out.append(_override(f"/{key}", key, (None, None)))
        elif key in UNSUPPORTED_PLAN:
            out.append(_unsupported(f"/{key}", key, (None, None)))
        elif key not in PLAN_FIELDS:
            schema(
                f"/{key}",
                f"unknown field {key!r}",
                observed=key,
                required=sorted(PLAN_FIELDS),
                suggestion="Remove it or correct the spelling. Unknown fields are not ignored.",
            )
    mode = data.get("mode", "sequential")
    if mode != "sequential":
        out.append(
            Diagnostic(
                "plan/unsupported-semantics",
                f"execution mode {mode!r} is not supported; only 'sequential' plans are checked",
                _loc("/mode"),
                observed=mode,
                required="sequential",
                suggestion="Submit an ordered sequential plan. Parallel schedules need their own "
                "semantics, which this version does not define.",
            )
        )
    for key in ("title", "description"):
        if key in data and (not isinstance(data[key], str) or len(data[key]) > MAX_TEXT):
            schema(f"/{key}", f"{key!r} must be a string of at most {MAX_TEXT} characters")

    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list):
        schema(
            "/steps",
            "'steps' must be a list",
            observed=type(raw_steps).__name__,
            suggestion='Add "steps": [ ... ] with one object per step, in execution order.',
        )
        return None, out
    if not raw_steps:
        schema("/steps", "the plan has no steps")
    if len(raw_steps) > MAX_STEPS:
        out.append(
            Diagnostic(
                "plan/limit",
                f"{len(raw_steps)} steps; the limit is {MAX_STEPS}",
                _loc("/steps"),
                observed=len(raw_steps),
                required=MAX_STEPS,
            )
        )
        return None, out

    steps: list[Step] = []
    first_seen: dict[str, int] = {}
    for i, raw in enumerate(raw_steps):
        path = f"/steps/{i}"
        if not isinstance(raw, dict):
            schema(path, f"step {i} must be an object", observed=type(raw).__name__)
            continue
        sid = raw.get("id")
        where = (sid if isinstance(sid, str) else None, i)
        ok = True
        for key in sorted(raw):
            if key in POLICY_FIELDS_STEP:
                out.append(_override(f"{path}/{key}", key, where))
            elif key in UNSUPPORTED_STEP:
                out.append(_unsupported(f"{path}/{key}", key, where))
                ok = False
            elif key not in STEP_FIELDS:
                out.append(
                    Diagnostic(
                        "plan/schema",
                        f"unknown step field {key!r}",
                        _loc(f"{path}/{key}", where),
                        observed=key,
                        required=sorted(STEP_FIELDS),
                        suggestion="Remove it or correct the spelling.",
                    )
                )
                ok = False
        if not isinstance(sid, str) or not STEP_ID.match(sid):
            out.append(
                Diagnostic(
                    "plan/schema",
                    f"step {i} needs an 'id' matching {STEP_ID.pattern}",
                    _loc(f"{path}/id", where),
                    observed=sid,
                    required=STEP_ID.pattern,
                )
            )
            ok = False
        action = raw.get("action")
        if not isinstance(action, str) or not ACTION_NAME.match(action):
            out.append(
                Diagnostic(
                    "plan/schema",
                    f"step {i} needs an 'action' string matching {ACTION_NAME.pattern}",
                    _loc(f"{path}/action", where),
                    observed=action,
                )
            )
            ok = False
        desc = raw.get("description", "")
        if not isinstance(desc, str) or len(desc) > MAX_TEXT:
            out.append(
                Diagnostic(
                    "plan/schema",
                    f"'description' must be a string of at most {MAX_TEXT} characters",
                    _loc(f"{path}/description", where),
                )
            )
            ok = False
        inputs = _inputs(raw.get("inputs", {}), path, where, out)
        if inputs is None:
            ok = False
        if isinstance(sid, str) and STEP_ID.match(sid):
            if sid in first_seen:
                out.append(
                    Diagnostic(
                        "plan/duplicate-step-id",
                        f"step id {sid!r} is used by steps[{first_seen[sid]}] and steps[{i}]",
                        _loc(f"{path}/id", where),
                        related=(_loc(f"/steps/{first_seen[sid]}/id", (sid, first_seen[sid])),),
                        observed=sid,
                        required="unique step ids",
                        suggestion="Give each step its own id; references name producers by id.",
                    )
                )
                ok = False
            else:
                first_seen[sid] = i
        if ok and isinstance(sid, str) and isinstance(action, str) and inputs is not None:
            steps.append(Step(i, sid, action, inputs, desc if isinstance(desc, str) else ""))

    blocking = [d for d in out if d.category != "violation"]
    if blocking:
        return None, out
    return Plan(tuple(steps), loaded.origin), out


def _inputs(
    raw: Any, path: str, where: tuple[str | None, int], out: list[Diagnostic]
) -> dict[str, str] | None:
    p = f"{path}/inputs"
    if not isinstance(raw, dict):
        out.append(
            Diagnostic(
                "plan/schema",
                "'inputs' must map input names to references",
                _loc(p, where),
                observed=type(raw).__name__,
            )
        )
        return None
    if len(raw) > MAX_INPUTS:
        out.append(
            Diagnostic(
                "plan/limit", f"{len(raw)} inputs; the limit is {MAX_INPUTS}", _loc(p, where)
            )
        )
        return None
    result: dict[str, str] = {}
    ok = True
    for name in sorted(raw):
        ref = raw[name]
        if not INPUT_NAME.match(name):
            out.append(
                Diagnostic(
                    "plan/schema",
                    f"input name {name!r} does not match {INPUT_NAME.pattern}",
                    _loc(f"{p}/{name}", where),
                    observed=name,
                )
            )
            ok = False
        elif not isinstance(ref, str) or not REFERENCE.match(ref):
            out.append(
                Diagnostic(
                    "plan/schema",
                    f'input {name!r} must be a reference "<step_id>.<output>"',
                    _loc(f"{p}/{name}", where),
                    observed=ref,
                    required="<step_id>.<output>",
                    suggestion='Refer to an earlier step\'s output, e.g. "build_rc.artifact".',
                )
            )
            ok = False
        else:
            result[name] = ref
    return result if ok else None


def _override(path: str, key: str, where: tuple[str | None, int | None]) -> Diagnostic:
    return Diagnostic(
        "plan/policy-override",
        f"the plan sets {key!r}, which only the policy owner may define; it was not honoured",
        _loc(path, where),
        observed=key,
        required=(
            "requirements, waivers and action definitions come from the owner contract and catalog"
        ),
        suggestion=f"Remove {key!r} from the plan. To change a requirement, the owner changes "
        "the contract.",
    )


def _unsupported(path: str, key: str, where: tuple[str | None, int | None]) -> Diagnostic:
    return Diagnostic(
        "plan/unsupported-semantics",
        f"field {key!r} asks for execution semantics this version does not support",
        _loc(path, where),
        observed=key,
        required="an ordered sequential list of steps",
        suggestion="Express the plan as an ordered list. Order is the list order, and "
        "dependencies come from the owner contract, not from the plan.",
    )
