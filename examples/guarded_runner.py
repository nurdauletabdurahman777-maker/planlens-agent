"""Dispatch only a plan that passed, and only the exact snapshot that passed.

Run from a directory created by `planlens-agent init`:

    cd release-basic && python guarded_runner.py

The handlers here only print. In a real integration each would call the
registered implementation of that action.
"""

import copy
import json

from planlens_agent import DispatchBlocked, NotApproved, ReferenceRunner, StepCall, check


def handler(call: StepCall):
    print(f"  dispatch {call.step_id:<12} {call.action:<14} inputs={dict(call.inputs)}")
    return {"artifact": f"<bytes built by {call.step_id}>"} if call.action == "build" else None


actions = ("build", "unit_tests", "security_scan", "code_review", "deploy", "notify")
runner = ReferenceRunner("contract.toml", "catalog.toml", {a: handler for a in actions})

print("1. a passing plan runs, in order, with the planned artifact:")
plan = json.load(open("plans/valid.json"))
approval = check(plan, "contract.toml", "catalog.toml").approve()
print("  binding:", approval.binding()["plan_sha256"][:16], "...")
runner.dispatch(approval, payload=plan)

print("2. a rejected plan cannot be approved, so nothing runs:")
rejected = check("plans/reversed-review.json", "contract.toml", "catalog.toml")
try:
    rejected.approve()
except NotApproved as exc:
    print(" ", exc)

print("3. a payload changed after the check is blocked until it is checked again:")
changed = copy.deepcopy(plan)
changed["steps"][4]["inputs"]["artifact"] = "build_hotfix.artifact"
try:
    runner.dispatch(approval, payload=changed)
except DispatchBlocked as exc:
    print(" ", exc, f"(dispatched: {exc.dispatched})")

print("4. mutating the original object after approval does not change what runs:")
plan["steps"].insert(0, {"id": "surprise", "action": "deploy"})
record = runner.dispatch(approval)  # runs the approved snapshot
print("  dispatched:", [sid for sid, _ in record.dispatched])
