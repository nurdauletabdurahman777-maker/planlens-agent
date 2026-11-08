"""Validated policy and approved plans cannot change underneath a check or a dispatch.

Regression tests for the second source review (A_REVIEW.md, round 2):

* F1 - a validated policy exposed mutable dicts; clearing an action's inputs
  turned a rejected plan into a pass while the digest stayed the same.
* F2 - the runner re-checked the plan, then dispatched from ``approval.plan``,
  a mapping the caller could still mutate during dispatch.
"""

from __future__ import annotations

import dataclasses
import json
import tomllib
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

import pytest

from planlens_agent import (
    DispatchBlocked,
    PolicyIntegrityError,
    ReferenceRunner,
    StepCall,
    check,
    validate_policy,
)

from helpers import TEMPLATE

CONTRACT, CATALOG = TEMPLATE / "contract.toml", TEMPLATE / "catalog.toml"
ACTIONS = ("build", "unit_tests", "security_scan", "code_review", "deploy", "notify")


def plan(name: str) -> dict[str, Any]:
    return json.loads((TEMPLATE / "plans" / name).read_text())


def unbound_plan() -> dict[str, Any]:
    """valid.json without the artifact bindings: rejected with plan/missing-input."""
    p = plan("valid.json")
    for s in p["steps"]:
        if s["action"] in ("unit_tests", "security_scan", "deploy"):
            del s["inputs"]
    return p


def mutable_containers(obj: Any, seen: set[int] | None = None) -> list[str]:
    """Every dict, list or set reachable from a validated policy object."""
    seen = seen if seen is not None else set()
    if id(obj) in seen or isinstance(obj, (str, int, float, bool, type(None))):
        return []
    seen.add(id(obj))
    found = [type(obj).__name__] if isinstance(obj, (dict, list, set)) else []
    if dataclasses.is_dataclass(obj):
        children: Any = [getattr(obj, f.name) for f in dataclasses.fields(obj)]
    elif isinstance(obj, Mapping):
        children = list(obj.values())
    elif isinstance(obj, (list, tuple, set, frozenset)):
        children = list(obj)
    else:
        children = []
    for child in children:
        found += mutable_containers(child, seen)
    return found


# --- F1: validated policy ---------------------------------------------------------------
def test_reviewer_reproduction_clearing_inputs_is_impossible() -> None:
    policy = validate_policy(CONTRACT, CATALOG)
    p = unbound_plan()
    assert {d.rule_id for d in check(p, policy=policy).diagnostics} == {"plan/missing-input"}
    for action in ("unit_tests", "security_scan", "deploy"):
        with pytest.raises((AttributeError, TypeError)):
            policy.catalog.actions[action].inputs.clear()  # type: ignore[union-attr,attr-defined]
        with pytest.raises(TypeError):
            policy.catalog.actions[action].inputs["artifact"] = "x"  # type: ignore[union-attr,index]
    with pytest.raises(TypeError):
        policy.catalog.actions["deploy"] = None  # type: ignore[union-attr,index]
    assert check(p, policy=policy).verdict == "reject"
    assert policy.verify()


def test_no_mutable_container_is_reachable_from_the_policy_model() -> None:
    policy = validate_policy(CONTRACT, CATALOG)
    assert policy.usable and policy.catalog is not None and policy.contract is not None
    assert len(policy.catalog.actions) == 6  # the walk visits real content
    assert mutable_containers(policy.catalog) == []
    assert mutable_containers(policy.contract) == []


def test_policy_is_independent_of_the_callers_mappings() -> None:
    catalog = tomllib.loads(CATALOG.read_text())
    contract = tomllib.loads(CONTRACT.read_text())
    policy = validate_policy(contract, catalog)
    catalog["actions"]["deploy"]["inputs"].clear()
    contract["required_actions"].clear()
    contract["rules"].clear()
    assert policy.verify()
    assert dict(policy.catalog.actions["deploy"].inputs) == {"artifact": "release-artifact"}  # type: ignore[union-attr]
    assert check(unbound_plan(), policy=policy).verdict == "reject"


def test_a_replaced_catalog_is_refused_under_its_old_identity() -> None:
    policy = validate_policy(CONTRACT, CATALOG)
    cat = policy.catalog
    assert cat is not None
    weakened = {name: dataclasses.replace(a, inputs={}) for name, a in cat.actions.items()}
    forged = dataclasses.replace(policy, catalog=dataclasses.replace(cat, actions=weakened))
    assert forged.catalog is not None and forged.catalog.digest == cat.digest
    assert not forged.verify()
    with pytest.raises(PolicyIntegrityError):
        check(unbound_plan(), policy=forged)


def test_a_replaced_contract_is_refused_under_its_old_identity() -> None:
    policy = validate_policy(CONTRACT, CATALOG)
    assert policy.contract is not None
    forged = dataclasses.replace(
        policy, contract=dataclasses.replace(policy.contract, required_actions=(), prerequisites=())
    )
    with pytest.raises(PolicyIntegrityError):
        check(plan("reversed-review.json"), policy=forged)


def test_replace_cannot_smuggle_in_a_mutable_mapping() -> None:
    action = validate_policy(CONTRACT, CATALOG).catalog.actions["deploy"]  # type: ignore[union-attr]
    raw = {"artifact": "release-artifact"}
    copy = dataclasses.replace(action, inputs=raw)
    raw.clear()
    assert dict(copy.inputs) == {"artifact": "release-artifact"}
    assert isinstance(copy.inputs, MappingProxyType)


def test_an_explicitly_changed_policy_gets_validated_with_a_new_identity(tmp_path: Any) -> None:
    old = validate_policy(CONTRACT, CATALOG)
    changed = tmp_path / "contract.toml"
    changed.write_text(CONTRACT.read_text().replace('"security_scan", ', ""))
    new = validate_policy(changed, CATALOG)
    assert new.usable and new.verify()
    assert new.contract is not None and old.contract is not None
    assert new.contract.digest != old.contract.digest
    assert new.contract.binding != old.contract.binding


def test_runner_held_policy_is_checked_at_dispatch() -> None:
    calls: list[str] = []

    def handler(call: StepCall) -> Mapping[str, Any] | None:
        calls.append(call.step_id)
        return {"artifact": "x"} if call.action == "build" else None

    runner = ReferenceRunner(CONTRACT, CATALOG, {a: handler for a in ACTIONS})
    approval = check(plan("valid.json"), CONTRACT, CATALOG).approve()
    cat = runner.policy.catalog
    assert cat is not None
    object.__setattr__(
        cat, "actions", {n: dataclasses.replace(a, inputs={}) for n, a in cat.actions.items()}
    )
    with pytest.raises(DispatchBlocked, match="runner's policy no longer matches"):
        runner.dispatch(approval)
    assert calls == []


# --- F2: what the runner dispatches -----------------------------------------------------
def _approved_mismatch_plan() -> tuple[dict[str, Any], Any]:
    p = plan("artifact-mismatch.json")
    p["steps"][5]["inputs"]["artifact"] = "build_rc.artifact"  # now it passes
    return p, check(p, CONTRACT, CATALOG).approve()


def _mutating_handlers(target: dict[str, Any], received: dict[str, Any]) -> dict[str, Any]:
    def handler(call: StepCall) -> Mapping[str, Any] | None:
        if call.step_id == "build_rc":  # first handler
            target["steps"][5]["inputs"]["artifact"] = "build_hotfix.artifact"
        if call.action == "deploy":
            received.update(call.inputs)
        return {"artifact": f"bytes-from-{call.step_id}"} if call.action == "build" else None

    return {a: handler for a in ACTIONS}


def test_reviewer_reproduction_replace_with_a_mutable_plan() -> None:
    p, approval = _approved_mismatch_plan()
    swapped = dataclasses.replace(approval, plan=p)  # public API
    received: dict[str, Any] = {}
    record = ReferenceRunner(CONTRACT, CATALOG, _mutating_handlers(p, received)).dispatch(swapped)
    assert record.status == "completed"
    assert received == {"artifact": "bytes-from-build_rc"}  # not build_hotfix
    assert p["steps"][5]["inputs"]["artifact"] == "build_hotfix.artifact"  # it was changed
    assert check(p, CONTRACT, CATALOG).verdict == "reject"  # and would not pass


def test_runner_never_reads_approval_plan_during_dispatch() -> None:
    """Second line of defence: even with ApprovedPlan's own copy bypassed, the runner
    dispatches the snapshot made by its own re-check."""
    p, approval = _approved_mismatch_plan()
    object.__setattr__(approval, "plan", p)  # bypass __post_init__
    received: dict[str, Any] = {}
    record = ReferenceRunner(CONTRACT, CATALOG, _mutating_handlers(p, received)).dispatch(approval)
    assert record.status == "completed"
    assert received == {"artifact": "bytes-from-build_rc"}


def test_replace_with_different_content_is_still_blocked() -> None:
    p, approval = _approved_mismatch_plan()
    changed = json.loads(json.dumps(p))
    changed["steps"][5]["inputs"]["artifact"] = "build_hotfix.artifact"
    calls: list[str] = []
    runner = ReferenceRunner(
        CONTRACT, CATALOG, {a: (lambda c: calls.append(c.step_id) or None) for a in ACTIONS}
    )
    with pytest.raises(DispatchBlocked):
        runner.dispatch(dataclasses.replace(approval, plan=changed))
    with pytest.raises(DispatchBlocked):
        runner.dispatch(approval, payload=changed)  # substitution stays blocked
    assert calls == []
