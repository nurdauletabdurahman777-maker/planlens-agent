"""Binding the check to execution (Standard section 3.6 and acceptance example 9).

Every test counts calls into handlers directly, so "zero dispatched" means no
handler was entered - not merely that a function returned an error.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from planlens_agent import (
    ApprovedPlan,
    DispatchBlocked,
    NotApproved,
    ReferenceRunner,
    StepCall,
    check,
)
from planlens_agent.loading import digest

from helpers import TEMPLATE

CONTRACT, CATALOG = TEMPLATE / "contract.toml", TEMPLATE / "catalog.toml"


class Recorder:
    """Handlers that record every call and pass artifacts along by value."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def handlers(self) -> dict[str, Any]:
        def make(action: str):
            def handler(call: StepCall) -> dict[str, Any] | None:
                self.calls.append((call.step_id, action, dict(call.inputs)))
                if action == "build":
                    return {"artifact": f"bytes-from-{call.step_id}"}
                return None

            return handler

        return {
            a: make(a)
            for a in ("build", "unit_tests", "security_scan", "code_review", "deploy", "notify")
        }


def plan(name: str) -> dict[str, Any]:
    return json.loads((TEMPLATE / "plans" / name).read_text())


def runner(rec: Recorder) -> ReferenceRunner:
    return ReferenceRunner(CONTRACT, CATALOG, rec.handlers())


def test_valid_plan_dispatches_in_order_with_planned_artifacts() -> None:
    rec = Recorder()
    approval = check(plan("valid.json"), CONTRACT, CATALOG).approve()
    record = runner(rec).dispatch(approval)
    assert record.status == "completed"
    assert [c[0] for c in rec.calls] == [
        "build_rc",
        "test_rc",
        "scan_rc",
        "review",
        "deploy_prod",
        "announce",
    ]
    deployed = next(c for c in rec.calls if c[1] == "deploy")
    assert deployed[2] == {"artifact": "bytes-from-build_rc"}


@pytest.mark.parametrize("name", ["reversed-review.json", "artifact-mismatch.json"])
def test_rejected_plan_cannot_be_approved_and_nothing_runs(name: str) -> None:
    rec = Recorder()
    result = check(plan(name), CONTRACT, CATALOG)
    assert result.verdict == "reject"
    with pytest.raises(NotApproved):
        result.approve()
    with pytest.raises(DispatchBlocked) as exc:
        runner(rec).dispatch(result)  # a CheckResult is not an approval
    assert exc.value.dispatched == 0
    assert rec.calls == []


def test_mutating_the_original_input_after_check_does_not_change_what_runs() -> None:
    """Mutation of the caller's object after the snapshot was taken: the unchanged
    validated snapshot is what runs."""
    rec = Recorder()
    original = plan("valid.json")
    approval = check(original, CONTRACT, CATALOG).approve()
    original["steps"][4]["inputs"]["artifact"] = "build_hotfix.artifact"
    original["steps"].insert(0, {"id": "rm_rf", "action": "deploy"})
    record = runner(rec).dispatch(approval)
    assert record.status == "completed"
    assert [c[0] for c in rec.calls][0] == "build_rc"
    assert "rm_rf" not in [c[0] for c in rec.calls]
    assert approval.plan["steps"][4]["inputs"]["artifact"] == "build_rc.artifact"


def test_snapshot_is_read_only() -> None:
    approval = check(plan("valid.json"), CONTRACT, CATALOG).approve()
    with pytest.raises(TypeError):
        approval.plan["steps"][4]["inputs"]["artifact"] = "x.artifact"  # type: ignore[index]
    with pytest.raises((TypeError, AttributeError)):
        approval.plan["steps"].append({})  # type: ignore[attr-defined]


def test_substituted_payload_with_changed_artifact_reference_is_blocked() -> None:
    """Acceptance example 9: an artifact reference changes after validation."""
    rec = Recorder()
    validated = plan("artifact-mismatch.json")
    validated["steps"][5]["inputs"]["artifact"] = "build_rc.artifact"  # make it valid
    approval = check(validated, CONTRACT, CATALOG).approve()

    substituted = copy.deepcopy(validated)
    substituted["steps"][5]["inputs"]["artifact"] = "build_hotfix.artifact"
    with pytest.raises(DispatchBlocked) as exc:
        runner(rec).dispatch(approval, payload=substituted)
    assert "not the approved plan" in exc.value.reason
    assert rec.calls == []

    # Revalidation of the substituted payload is what decides - here, it is rejected.
    assert check(substituted, CONTRACT, CATALOG).verdict == "reject"


def test_substituted_payload_is_blocked_even_when_it_would_pass() -> None:
    """A changed payload is blocked until re-checked, even a valid one. The runner
    never re-validates a substituted payload on the caller's behalf."""
    rec = Recorder()
    approval = check(plan("valid.json"), CONTRACT, CATALOG).approve()
    other_valid = plan("valid-alternate-order.json")
    assert check(other_valid, CONTRACT, CATALOG).verdict == "pass"
    with pytest.raises(DispatchBlocked):
        runner(rec).dispatch(approval, payload=other_valid)
    assert rec.calls == []
    # After revalidation it gets its own approval and may run.
    record = runner(rec).dispatch(
        check(other_valid, CONTRACT, CATALOG).approve(), payload=other_valid
    )
    assert record.status == "completed"


def test_presented_payload_equal_to_approval_runs_the_snapshot() -> None:
    rec = Recorder()
    p = plan("valid.json")
    approval = check(p, CONTRACT, CATALOG).approve()
    assert runner(rec).dispatch(approval, payload=copy.deepcopy(p)).status == "completed"


def test_tampered_approval_snapshot_is_blocked() -> None:
    rec = Recorder()
    approval = check(plan("valid.json"), CONTRACT, CATALOG).approve()
    bad = plan("valid.json")
    bad["steps"][4]["inputs"]["artifact"] = "build_rc.artifact"
    bad["steps"].append(
        {"id": "extra_deploy", "action": "deploy", "inputs": {"artifact": "build_rc.artifact"}}
    )
    from planlens_agent.loading import freeze

    object.__setattr__(approval, "plan", freeze(bad))
    with pytest.raises(DispatchBlocked) as exc:
        runner(rec).dispatch(approval)
    assert "no longer matches" in exc.value.reason
    assert rec.calls == []


def test_hand_built_approval_for_a_rejected_plan_is_blocked() -> None:
    """Anyone can construct an ApprovedPlan; the runner re-checks the snapshot under
    its own policy, so a forged approval of a bad plan still does not run."""
    rec = Recorder()
    good = check(plan("valid.json"), CONTRACT, CATALOG).approve()
    from planlens_agent.loading import freeze

    bad = plan("reversed-review.json")
    forged = ApprovedPlan(
        freeze(bad),
        digest(bad),
        good.contract_id,
        good.contract_version,
        good.contract_digest,
        good.catalog_id,
        good.catalog_version,
        good.catalog_digest,
        good.engine_version,
    )
    with pytest.raises(DispatchBlocked) as exc:
        runner(rec).dispatch(forged)
    assert "does not pass under this runner's policy" in exc.value.reason
    assert rec.calls == []


def test_approval_under_a_different_contract_is_blocked(tmp_path: Path) -> None:
    rec = Recorder()
    looser = tmp_path / "looser.toml"
    text = CONTRACT.read_text()
    looser.write_text(
        text.split("[[rules]]")[0].replace('version = "1.0.0"\n', 'version = "1.0.0-loose"\n', 1)
    )
    approval = check(
        plan("reversed-review.json"), looser, CATALOG
    ).approve()  # passes the looser one
    with pytest.raises(DispatchBlocked) as exc:
        runner(rec).dispatch(approval)  # runner holds the real contract
    assert "different contract" in exc.value.reason
    assert rec.calls == []


def test_missing_handler_blocks_before_any_dispatch() -> None:
    rec = Recorder()
    handlers = rec.handlers()
    del handlers["notify"]  # the last step's action
    approval = check(plan("valid.json"), CONTRACT, CATALOG).approve()
    with pytest.raises(DispatchBlocked):
        ReferenceRunner(CONTRACT, CATALOG, handlers).dispatch(approval)
    assert rec.calls == []  # not even build ran


def test_handler_failure_stops_without_retry() -> None:
    rec = Recorder()
    handlers = rec.handlers()

    def failing(call: StepCall) -> None:
        rec.calls.append((call.step_id, "security_scan", {}))
        raise RuntimeError("scanner unavailable")

    handlers["security_scan"] = failing
    record = ReferenceRunner(CONTRACT, CATALOG, handlers).dispatch(
        check(plan("valid.json"), CONTRACT, CATALOG).approve()
    )
    assert record.status == "handler_failed" and record.failed_step == "scan_rc"
    assert [c[0] for c in rec.calls] == ["build_rc", "test_rc", "scan_rc"]


def test_runner_refuses_a_policy_it_cannot_enforce() -> None:
    unsupported = Path(__file__).parent / "acceptance" / "contracts" / "unsupported-rule.toml"
    with pytest.raises(ValueError, match="cannot fully enforce"):
        ReferenceRunner(unsupported, CATALOG, Recorder().handlers())


def test_runner_refuses_handlers_for_unknown_actions() -> None:
    with pytest.raises(ValueError, match="not in the catalog"):
        ReferenceRunner(CONTRACT, CATALOG, {"format_disk": lambda call: None})
