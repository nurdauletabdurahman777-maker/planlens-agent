"""The LangGraph gate, run against the real package.

Needs ``requirements.lock`` and an installed planlens-agent. Run from anywhere:

    python -m pytest examples/langgraph_gate/test_gate.py
"""

from __future__ import annotations

import copy
import json
import socket
import subprocess
import sys
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
from gate import FixturePlanner, build_gate

from planlens_agent import StepCall, check

TEMPLATE = Path(str(resources.files("planlens_agent").joinpath("templates", "release_basic")))
CONTRACT, CATALOG = TEMPLATE / "contract.toml", TEMPLATE / "catalog.toml"
ACTIONS = ("build", "unit_tests", "security_scan", "code_review", "deploy", "notify")


class Handlers(dict):
    """Instrumented handlers. ``calls`` is the only evidence of what ran."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[str] = []

        def handler(call: StepCall) -> dict[str, Any] | None:
            self.calls.append(call.step_id)
            if call.step_id == fail_on:
                raise RuntimeError("handler failed")
            return {"artifact": "bytes"} if call.action == "build" else None

        super().__init__({a: handler for a in ACTIONS})


def plan(name: str) -> dict[str, Any]:
    return json.loads((TEMPLATE / "plans" / name).read_text())


def run(planner: Any, handlers: Handlers | None = None, **options: Any) -> tuple[Any, Handlers]:
    handlers = handlers if handlers is not None else Handlers()
    gate = build_gate(planner, CONTRACT, CATALOG, handlers, **options)
    return gate.invoke({"request": "release"}), handlers


def test_the_graph_is_planner_check_then_execute_or_reject() -> None:
    gate = build_gate(lambda r: {}, CONTRACT, CATALOG, Handlers())
    drawn = gate.get_graph()
    assert {"planner", "check", "execute", "reject", "error"} <= set(drawn.nodes)
    edges = {(e.source, e.target) for e in drawn.edges}
    assert {("planner", "check"), ("check", "execute"), ("check", "reject")} <= edges
    assert ("planner", "execute") not in edges


def test_a_passing_plan_executes_the_approved_snapshot_in_order() -> None:
    state, handlers = run(lambda r: plan("valid.json"))
    assert state["status"] == "executed"
    expected = [s["id"] for s in plan("valid.json")["steps"]]
    assert handlers.calls == expected
    assert [sid for sid, _ in state["dispatched"]] == expected
    direct = check(plan("valid.json"), CONTRACT, CATALOG)
    assert state["approval"].binding()["plan_sha256"] == direct.plan_digest


@pytest.mark.parametrize(
    ("name", "rule"),
    [
        ("artifact-mismatch.json", "plan/artifact-identity"),
        ("reversed-review.json", "plan/prerequisite-order"),
    ],
)
def test_a_rejected_plan_calls_no_handler(name: str, rule: str) -> None:
    state, handlers = run(lambda r: plan(name))
    assert state["status"] == "rejected"
    assert handlers.calls == []
    assert state["dispatched"] == []
    assert state["approval"] is None
    assert rule in state["reason"]


def test_the_mismatch_and_its_passing_counterpart_differ_only_by_verdict() -> None:
    bad, bad_handlers = run(lambda r: plan("artifact-mismatch.json"))
    fixed = plan("artifact-mismatch.json")
    for s in fixed["steps"]:
        if s["action"] == "deploy":
            s["inputs"]["artifact"] = "build_rc.artifact"
    good, good_handlers = run(lambda r: fixed)
    assert (bad["status"], len(bad_handlers.calls)) == ("rejected", 0)
    assert (good["status"], len(good_handlers.calls)) == ("executed", len(fixed["steps"]))


def test_unsupported_fields_are_not_stripped_on_the_way_to_the_checker() -> None:
    candidate = plan("valid.json")
    candidate["steps"][0]["parallel"] = True
    state, handlers = run(lambda r: candidate)
    assert state["status"] != "executed"
    assert handlers.calls == []
    assert state["plan"]["steps"][0]["parallel"] is True
    assert state["report"]["diagnostics"]


def test_a_plan_cannot_waive_the_contract() -> None:
    candidate = plan("reversed-review.json")
    candidate["waive"] = ["review-before-deploy"]
    state, handlers = run(lambda r: candidate)
    assert state["status"] != "executed"
    assert handlers.calls == []


def test_an_invalid_plan_takes_the_error_path() -> None:
    state, handlers = run(lambda r: {"schema": "planlens-agent/plan/v1"})
    assert state["status"] == "error"
    assert handlers.calls == []


def test_a_string_from_the_planner_is_never_read_as_a_path() -> None:
    state, handlers = run(lambda r: str(TEMPLATE / "plans" / "valid.json"))
    assert state["status"] == "error"
    assert "not a mapping" in state["reason"]
    assert handlers.calls == []
    assert state["report"] is None  # the checker was never given it


def test_a_failing_planner_takes_the_error_path() -> None:
    def planner(request: str) -> dict[str, Any]:
        raise TimeoutError("planner timed out")

    state, handlers = run(planner)
    assert state["status"] == "error"
    assert "planner timed out" in state["reason"]
    assert handlers.calls == []


def test_a_plan_replaced_after_the_check_is_blocked() -> None:
    def replace(state: Any) -> Any:
        changed = copy.deepcopy(state["plan"])
        changed["steps"].insert(0, {"id": "surprise", "action": "notify"})
        return {"plan": changed}

    state, handlers = run(lambda r: plan("valid.json"), after_check=replace)
    assert state["status"] == "blocked"
    assert handlers.calls == []


def test_a_plan_edited_in_place_after_the_check_is_blocked() -> None:
    def edit(state: Any) -> Any:
        state["plan"]["steps"][4]["inputs"]["artifact"] = "elsewhere.artifact"
        return {}

    state, handlers = run(lambda r: plan("valid.json"), after_check=edit)
    assert state["status"] == "blocked"
    assert handlers.calls == []


def test_a_handler_failure_stops_the_run_and_is_reported() -> None:
    state, handlers = run(lambda r: plan("valid.json"), Handlers(fail_on="scan_rc"))
    assert state["status"] == "handler_failed"
    assert handlers.calls == ["build_rc", "test_rc", "scan_rc"]


def test_the_fixture_planner_says_what_it_is(tmp_path: Path) -> None:
    assert "not model output" in FixturePlanner(tmp_path / "x.json").label


def test_the_gate_makes_no_network_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("network connection attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    state, _ = run(lambda r: plan("valid.json"))
    assert state["status"] == "executed"


@pytest.mark.parametrize(
    ("name", "code", "calls"),
    [("valid.json", 0, "handler calls: 6"), ("artifact-mismatch.json", 1, "handler calls: 0")],
)
def test_the_script_reports_its_outcome_as_an_exit_status(name: str, code: int, calls: str) -> None:
    script = Path(__file__).with_name("gate.py")
    done = subprocess.run(
        [sys.executable, str(script), f"plans/{name}"],
        capture_output=True,
        text=True,
        cwd=TEMPLATE,
    )
    assert done.returncode == code, done.stderr
    assert calls in done.stdout
    assert "authored fixture, not model output" in done.stdout


# --- only this invocation's check can authorize its execution -----------------------
def approval_for(name: str = "valid.json") -> Any:
    return check(plan(name), CONTRACT, CATALOG).approve()


class FailsOnSecondCall:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self, request: str) -> dict[str, Any]:
        self.count += 1
        if self.count == 2:
            raise TimeoutError("second call")
        return plan("valid.json")


def test_the_returned_state_cannot_authorize_a_failing_second_invocation() -> None:
    handlers = Handlers()
    gate = build_gate(FailsOnSecondCall(), CONTRACT, CATALOG, handlers)
    first = gate.invoke({"request": "first"})
    assert (first["status"], len(handlers.calls)) == ("executed", 6)
    second = gate.invoke({**first, "request": "second"})
    assert second["status"] == "error"
    assert "second call" in second["reason"]
    assert len(handlers.calls) == 6  # the second invocation added none
    assert second["approval"] is None
    assert second["plan"] is None
    assert second["dispatched"] == []


@pytest.mark.parametrize(
    ("output", "status"),
    [
        (None, "error"),
        ("plans/valid.json", "error"),
        ({"schema": "planlens-agent/plan/v1"}, "error"),
    ],
)
def test_a_prior_approval_in_the_input_authorizes_nothing(output: Any, status: str) -> None:
    handlers = Handlers()
    gate = build_gate(lambda r: output, CONTRACT, CATALOG, handlers)
    state = gate.invoke(
        {"request": "r", "approval": approval_for(), "plan": plan("valid.json"), "status": ""}
    )
    assert state["status"] == status
    assert handlers.calls == []


def test_a_prior_approval_does_not_rescue_a_rejected_plan() -> None:
    handlers = Handlers()
    gate = build_gate(lambda r: plan("reversed-review.json"), CONTRACT, CATALOG, handlers)
    state = gate.invoke({"request": "r", "approval": approval_for()})
    assert state["status"] == "rejected"
    assert state["approval"] is None
    assert handlers.calls == []


def test_caller_supplied_outcome_fields_are_not_read() -> None:
    handlers = Handlers()
    gate = build_gate(lambda r: plan("valid.json"), CONTRACT, CATALOG, handlers)
    state = gate.invoke(
        {"request": "r", "status": "error", "reason": "stale", "report": {"old": 1}}
    )
    assert (state["status"], state["reason"]) == ("executed", "")
    assert "old" not in state["report"]
    assert len(handlers.calls) == 6


def test_a_gate_recovers_after_an_earlier_error() -> None:
    outcomes: list[Any] = [TimeoutError("down"), "not a plan", plan("valid.json")]

    def planner(request: str) -> Any:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    handlers = Handlers()
    gate = build_gate(planner, CONTRACT, CATALOG, handlers)
    state = gate.invoke({"request": "1"})
    assert (state["status"], handlers.calls) == ("error", [])
    state = gate.invoke({**state, "request": "2"})
    assert (state["status"], handlers.calls) == ("error", [])
    state = gate.invoke({**state, "request": "3"})
    assert (state["status"], len(handlers.calls)) == ("executed", 6)


def test_every_invocation_starts_clean_even_if_old_state_reaches_the_planner_node() -> None:
    def planner(request: str) -> Any:
        raise TimeoutError("down")

    gate = build_gate(planner, CONTRACT, CATALOG, Handlers())
    node = gate.builder.nodes["planner"].runnable
    update = node.invoke({"request": "r", "approval": approval_for(), "plan": plan("valid.json")})
    assert update["approval"] is None
    assert update["plan"] is None
    assert update["status"] == "error"


@pytest.mark.parametrize(
    "update",
    [{"plan": None}, {"plan": "plans/valid.json"}, {"plan": []}, {"plan": {}}, {"approval": None}],
    ids=["none", "string", "list", "empty", "approval-removed"],
)
def test_execution_needs_the_current_plan_and_approval(update: dict[str, Any]) -> None:
    state, handlers = run(lambda r: plan("valid.json"), after_check=lambda s: update)
    assert state["status"] == "blocked"
    assert handlers.calls == []


def test_the_graph_accepts_only_a_request_as_input() -> None:
    gate = build_gate(lambda r: plan("valid.json"), CONTRACT, CATALOG, Handlers())
    assert set(gate.get_input_jsonschema()["properties"]) == {"request"}
