"""A LangGraph gate: planner -> PlanLens check -> execute or reject.

Run from a directory created by ``planlens-agent init``::

    python gate.py plans/valid.json              # exit 0, handlers run
    python gate.py plans/artifact-mismatch.json  # exit 1, no handler runs

The planner here is a ``FixturePlanner``: it returns an authored file and is not a model.
To connect a real planner, pass any callable that takes the request and returns the
candidate plan as a mapping. See README.md.

The contract and catalog are given to ``build_gate`` by the application. Nothing in the
graph state can replace them, and the plan is passed to ``check()`` as it arrived: this
recipe never removes a field the checker would refuse.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict  # Pydantic needs this one on Python < 3.12

from planlens_agent import ApprovedPlan, DispatchBlocked, ReferenceRunner, StepCall, check

Planner = Callable[[str], Mapping[str, Any]]
Handler = Callable[[StepCall], Mapping[str, Any] | None]

EXIT = {"executed": 0, "rejected": 1}  # anything else is 2: nothing was shown to be safe


class GateInput(TypedDict):
    """All a caller supplies. Anything else in the input is not read."""

    request: str


class GateState(TypedDict, total=False):
    request: str
    plan: Any  # the candidate, exactly as the planner returned it
    approval: ApprovedPlan | None  # set only by this invocation's check
    status: str  # executed, rejected, error, blocked or handler_failed
    report: dict[str, Any] | None  # the check report
    reason: str
    dispatched: list[tuple[str, str]]


class FixturePlanner:
    """Returns an authored plan from a file. This is a fixture, not a model."""

    label = "authored fixture, not model output"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def __call__(self, request: str) -> Mapping[str, Any]:
        loaded: Mapping[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        return loaded


def build_gate(
    planner: Planner,
    contract: str | Path,
    catalog: str | Path,
    handlers: Mapping[str, Handler],
    *,
    after_check: Callable[[GateState], GateState] | None = None,
) -> Any:
    """Compile the graph. ``after_check`` adds a node between the check and execution.

    That node stands for anything else in your graph that sees the state after the
    check. It cannot change what runs: see ``execute``.
    """
    runner = ReferenceRunner(contract, catalog, handlers)

    def plan_node(state: GateState) -> GateState:
        # Every invocation starts clean, so nothing earlier can authorize this one.
        fresh: GateState = {
            "plan": None,
            "approval": None,
            "report": None,
            "status": "",
            "reason": "",
            "dispatched": [],
        }
        try:
            return {**fresh, "plan": planner(state["request"])}
        except Exception as exc:  # no plan means no execution, and the reason is kept
            return {
                **fresh,
                "status": "error",
                "reason": f"planner failed: {type(exc).__name__}: {exc}",
            }

    def check_node(state: GateState) -> GateState:
        if state.get("status") == "error":
            return {}
        plan = state.get("plan")
        # check() reads a string as a path, so planner text must never reach it as a string.
        if not isinstance(plan, Mapping):
            return {
                "status": "error",
                "reason": f"planner returned {type(plan).__name__}, not a mapping",
            }
        result = check(plan, policy=runner.policy)
        update: GateState = {"report": result.to_dict(), "approval": None}
        if result.passed:
            update["approval"] = result.approve()
        elif result.verdict == "reject":
            update["status"] = "rejected"
        else:
            update["status"] = "error"
            update["reason"] = f"plan could not be evaluated: {result.error_kind}"
        return update

    def route(state: GateState) -> str:
        if isinstance(state.get("approval"), ApprovedPlan):
            return "approved"
        return "rejected" if state.get("status") == "rejected" else "error"

    def execute_node(state: GateState) -> GateState:
        # The runner executes its own approved snapshot. Passing the plan from the state as
        # the payload makes it refuse if that plan no longer matches what was approved. The
        # runner reads a payload of None as "nothing to compare", so that is refused here.
        plan = state.get("plan")
        if not isinstance(plan, Mapping):
            return {
                "status": "blocked",
                "reason": "there is no current plan to compare with the approval",
                "dispatched": [],
            }
        try:
            record = runner.dispatch(state.get("approval"), payload=plan)
        except DispatchBlocked as exc:
            return {"status": "blocked", "reason": exc.reason, "dispatched": []}
        status = "executed" if record.status == "completed" else "handler_failed"
        return {"status": status, "reason": record.error or "", "dispatched": record.dispatched}

    def reject_node(state: GateState) -> GateState:
        found = [d["rule_id"] for d in state["report"]["diagnostics"]]
        return {"reason": "plan rejected: " + ", ".join(found), "dispatched": []}

    def error_node(state: GateState) -> GateState:
        return {"dispatched": []}

    graph = StateGraph(GateState, input_schema=GateInput)
    graph.add_node("planner", plan_node)
    graph.add_node("check", check_node)
    graph.add_node("execute", execute_node)
    graph.add_node("reject", reject_node)
    graph.add_node("error", error_node)
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "check")
    approved = "execute"
    if after_check is not None:
        graph.add_node("after_check", after_check)
        graph.add_edge("after_check", "execute")
        approved = "after_check"
    graph.add_conditional_edges(
        "check", route, {"approved": approved, "rejected": "reject", "error": "error"}
    )
    for node in ("execute", "reject", "error"):
        graph.add_edge(node, END)
    # No checkpointer: an approval is an in-memory object and is not meant to be persisted.
    return graph.compile()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python gate.py PLAN.json", file=sys.stderr)
        return 2
    calls: list[str] = []

    def handler(call: StepCall) -> Mapping[str, Any] | None:
        calls.append(call.step_id)
        return {"artifact": f"<built by {call.step_id}>"} if call.action == "build" else None

    actions = ("build", "unit_tests", "security_scan", "code_review", "deploy", "notify")
    planner = FixturePlanner(argv[1])
    gate = build_gate(planner, "contract.toml", "catalog.toml", {a: handler for a in actions})
    state = gate.invoke({"request": f"release using {argv[1]}"})
    print(f"planner:       {planner.label}")
    print(f"status:        {state['status']}")
    if state.get("reason"):
        print(f"reason:        {state['reason']}")
    print(f"handler calls: {len(calls)} {calls}")
    return EXIT.get(state["status"], 2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
