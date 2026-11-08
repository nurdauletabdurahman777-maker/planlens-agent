"""Regression fixtures: known plans with their expected outcomes.

A fixture says "under this contract and catalog, this plan should pass" or
"should be rejected for exactly these rules". Running fixtures checks that the
checker still behaves as recorded.

Two different meanings are kept apart on every line of output:

* whether the fixture's *expectation* was met - a property of the checker;
* whether the *plan* passes - a property of the plan.

A rejected plan whose rejection was expected is a met expectation. It is still
a rejected plan, and nothing here approves it for execution.

Replaying fixtures checks known policy behaviour. It does not evaluate a new
planner or model; that needs fresh candidate plans.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .checker import CheckResult, check
from .loading import load
from .policy import Policy, validate_policy

FIXTURES_SCHEMA = "planlens-agent/fixtures/v1"
VERDICTS = ("pass", "reject", "error")


class FixtureFileError(Exception):
    """The fixture file itself is unusable."""


@dataclass(frozen=True)
class CaseResult:
    name: str
    plan: str
    expected_verdict: str
    observed_verdict: str
    expected_rules: tuple[str, ...] | None
    observed_rules: tuple[str, ...]
    met: bool
    plan_passes: bool
    result: CheckResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "plan": self.plan,
            "expected_verdict": self.expected_verdict,
            "observed_verdict": self.observed_verdict,
            "expected_rules": list(self.expected_rules)
            if self.expected_rules is not None
            else None,
            "observed_rules": list(self.observed_rules),
            "expectation_met": self.met,
            "plan_passes_required_checks": self.plan_passes,
        }


@dataclass(frozen=True)
class FixtureRun:
    path: str
    policy: Policy
    cases: tuple[CaseResult, ...]

    @property
    def all_met(self) -> bool:
        return all(c.met for c in self.cases)

    def to_dict(self) -> dict[str, Any]:
        con, cat = self.policy.contract, self.policy.catalog
        return {
            "fixtures_schema": FIXTURES_SCHEMA,
            "fixtures": self.path,
            "contract": f"{con.id} {con.version}" if con else None,
            "catalog": f"{cat.id} {cat.version}" if cat else None,
            "cases": [c.to_dict() for c in self.cases],
            "expectations_met": sum(c.met for c in self.cases),
            "expectations_total": len(self.cases),
            "note": "A met expectation for a rejected plan does not approve that plan.",
        }


def run_fixtures(path: str | Path) -> FixtureRun:
    path = Path(path)
    try:
        found = path.is_file()
    except OSError as exc:
        raise FixtureFileError(f"cannot read fixture file: {exc.strerror}") from None
    if not found:
        raise FixtureFileError(f"fixture file not found: {path}")
    loaded = load(path, "plan")
    if loaded.data is None:
        raise FixtureFileError("; ".join(d.message for d in loaded.diagnostics))
    data = loaded.data
    if data.get("schema") != FIXTURES_SCHEMA:
        raise FixtureFileError(f"'schema' must be {FIXTURES_SCHEMA!r}")
    unknown = sorted(set(data) - {"schema", "contract", "catalog", "cases", "description"})
    if unknown:
        raise FixtureFileError(f"unknown fields {unknown}")
    for key in ("contract", "catalog"):
        if not isinstance(data.get(key), str):
            raise FixtureFileError(f"{key!r} must be a path relative to the fixture file")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise FixtureFileError("'cases' must be a non-empty list")

    base = path.parent
    policy = validate_policy(base / data["contract"], base / data["catalog"])
    results: list[CaseResult] = []
    names: set[str] = set()
    for i, case in enumerate(cases):
        if not isinstance(case, dict):
            raise FixtureFileError(f"cases[{i}] must be a table")
        extra = sorted(set(case) - {"name", "plan", "expect", "expect_rules", "note"})
        if extra:
            raise FixtureFileError(f"cases[{i}] has unknown fields {extra}")
        name, plan, expect = case.get("name"), case.get("plan"), case.get("expect")
        if not isinstance(name, str) or not name or name in names:
            raise FixtureFileError(f"cases[{i}] needs a unique string 'name'")
        names.add(name)
        if not isinstance(plan, str):
            raise FixtureFileError(f"case {name!r} needs a 'plan' path")
        if expect not in VERDICTS:
            raise FixtureFileError(f"case {name!r}: 'expect' must be one of {VERDICTS}")
        rules = case.get("expect_rules")
        if rules is not None and (
            not isinstance(rules, list) or not all(isinstance(r, str) for r in rules)
        ):
            raise FixtureFileError(f"case {name!r}: 'expect_rules' must be a list of rule ids")
        res = check(base / plan, policy=policy)
        observed = tuple(sorted({d.rule_id for d in res.diagnostics}))
        expected = tuple(sorted(set(rules))) if rules is not None else None
        met = res.verdict == expect and (expected is None or expected == observed)
        results.append(
            CaseResult(name, plan, expect, res.verdict, expected, observed, met, res.passed, res)
        )
    return FixtureRun(str(path), policy, tuple(results))


def render_fixture_run(run: FixtureRun) -> str:
    con, cat = run.policy.contract, run.policy.catalog
    lines = [f"regression fixtures: {run.path}"]
    if con and cat:
        lines.append(f"  contract {con.id} {con.version}, catalog {cat.id} {cat.version}")
    lines.append("")
    width = max(len(c.name) for c in run.cases)
    for c in run.cases:
        status = "met  " if c.met else "UNMET"
        line = (
            f"  {status}  {c.name:<{width}}  expected {c.expected_verdict:<6}  "
            f"observed {c.observed_verdict:<6}"
        )
        if c.observed_rules:
            line += f"  rules: {', '.join(c.observed_rules)}"
        if not c.plan_passes:
            line += "  [plan not approved for execution]"
        lines.append(line.rstrip())
        if not c.met and c.expected_rules is not None and c.expected_rules != c.observed_rules:
            lines.append(f"         expected rules: {', '.join(c.expected_rules) or '(none)'}")
    met = sum(c.met for c in run.cases)
    lines.append("")
    lines.append(f"{met} of {len(run.cases)} expectations met.")
    lines.append(
        "A met expectation for a rejected plan means the checker behaved as recorded. "
        "It does not approve that plan for execution."
    )
    return "\n".join(lines) + "\n"
