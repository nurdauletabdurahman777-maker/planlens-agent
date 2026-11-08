"""Standard section 7 acceptance examples, run through the SDK and the CLI.

Each case is an authored fixture. The test asserts the verdict, the exit code,
the exact number of findings per rule (so a dropped cause fails), the
locations that must be meaningful, and the ones that must be explicitly
absent. The CLI must agree with the SDK on every case.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import pytest

from planlens_agent import CheckResult, check

from helpers import ACCEPTANCE, load_cases, run_cli

DEFAULTS, CASES = load_cases()


def _paths(case: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(ACCEPTANCE / case["plan"]),
        str(ACCEPTANCE / case.get("contract", DEFAULTS["contract"])),
        str(ACCEPTANCE / case.get("catalog", DEFAULTS["catalog"])),
    )


def assert_case(case: dict[str, Any], result: CheckResult) -> None:
    """Every expectation a case records. Also used by the mutation tests."""
    exit_codes = {"pass": 0, "reject": 1, "error": 2}
    assert result.verdict == case["verdict"], [d.rule_id for d in result.diagnostics]
    assert result.exit_code == exit_codes[case["verdict"]]
    assert result.error_kind == case.get("error_kind")
    assert dict(Counter(d.rule_id for d in result.diagnostics)) == case["rules"]
    for want in case.get("locations", []):
        hits = [
            d
            for d in result.diagnostics
            if d.rule_id == want["rule"]
            and d.location is not None
            and d.location.path == want["path"]
            and d.location.document == want.get("document", "plan")
        ]
        assert hits, f"no {want['rule']} finding at {want['path']}"
        if "step_id" in want:
            assert hits[0].location is not None
            assert hits[0].location.step_id == want["step_id"]
            assert hits[0].location.step_index is not None
    for rule in case.get("absent", []):
        for d in result.diagnostics:
            if d.rule_id == rule:
                assert d.location is None and d.location_absent_reason
    if "source_span" in case:
        spans = [
            d.location.source_span
            for d in result.diagnostics
            if d.location is not None and d.location.source_span is not None
        ]
        assert [(s.line, s.column) for s in spans] == [
            (case["source_span"]["line"], case["source_span"]["column"])
        ]
    if case["verdict"] != "pass":
        assert not result.passed


def test_every_standard_example_is_covered() -> None:
    covered = {c["standard"] for c in CASES}
    # Example 9 (plan changed after validation) is covered in test_runner.py.
    assert covered == set(range(1, 11)) - {9}


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_sdk(case: dict[str, Any]) -> None:
    plan, contract, catalog = _paths(case)
    assert_case(case, check(plan, contract, catalog))


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_cli_agrees_with_sdk(case: dict[str, Any]) -> None:
    plan, contract, catalog = _paths(case)
    sdk = check(plan, contract, catalog)
    proc = run_cli("check", plan, "--contract", contract, "--catalog", catalog, "--format", "json")
    assert proc.returncode == sdk.exit_code
    assert "Traceback" not in proc.stderr
    report = json.loads(proc.stdout)  # stdout is exactly one JSON document
    assert report == sdk.to_dict()


def test_unsupported_rule_negative_control() -> None:
    """The plan in the unsupported-rule case passes once that rule is gone, so the
    error is caused by the unsupported rule and not by the plan."""
    case = next(c for c in CASES if c["name"] == "contract-requires-an-unsupported-rule")
    plan, _, catalog = _paths(case)
    assert check(plan, str(ACCEPTANCE / DEFAULTS["contract"]), catalog).verdict == "pass"
    assert check(plan, *_paths(case)[1:]).verdict == "error"


def test_waiver_is_not_honoured() -> None:
    """Removing the waiver field changes nothing about the requirements found."""
    case = next(c for c in CASES if c["name"] == "candidate-tries-to-waive-review")
    plan_path, contract, catalog = _paths(case)
    data = json.loads(open(plan_path).read())
    with_waiver = check(data, contract, catalog)
    del data["waive"]
    del data["steps"][3]["approved"]
    without = check(data, contract, catalog)

    def strip(r: CheckResult) -> list[str]:
        return sorted(d.rule_id for d in r.diagnostics if d.rule_id != "plan/policy-override")

    assert (
        strip(with_waiver)
        == strip(without)
        == ["plan/missing-required-action", "plan/prerequisite-order"]
    )
