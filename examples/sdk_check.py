"""Check plans from Python, with the same result the CLI gives.

Run from a directory created by `planlens-agent init`:

    cd release-basic && python sdk_check.py
"""

from planlens_agent import check, validate_policy

policy = validate_policy("contract.toml", "catalog.toml")  # validate once, reuse
assert policy.usable, [d.rule_id for d in policy.diagnostics]

for path in ("plans/valid.json", "plans/reversed-review.json", "plans/artifact-mismatch.json"):
    result = check(path, policy=policy)
    print(f"{path}: {result.verdict} (exit {result.exit_code})")
    for d in result.diagnostics:
        where = d.location.path if d.location else d.location_absent_reason
        print(f"  {d.rule_id} at {where}")
        print(f"    {d.message}")
        print(f"    next: {d.suggestion}")

# A plan produced in-process, e.g. parsed from an agent's structured output:
agent_plan = {
    "schema": "planlens-agent/plan/v1",
    "steps": [
        {"id": "b", "action": "build"},
        {"id": "t", "action": "unit_tests", "inputs": {"artifact": "b.artifact"}},
        {"id": "s", "action": "security_scan", "inputs": {"artifact": "b.artifact"}},
        {"id": "r", "action": "code_review"},
        {"id": "d", "action": "deploy", "inputs": {"artifact": "b.artifact"}},
    ],
}
result = check(agent_plan, policy=policy)
print(f"in-memory plan: {result.verdict}")
