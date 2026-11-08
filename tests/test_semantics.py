"""Behavioural checks that do not rely on the authored examples alone.

1. An independent oracle. For the release-basic contract, the constraints on
   one five-step plan are written out by hand here, without using the
   checker's code, and compared with the checker on all 120 orderings - both
   the verdict and the set of rules reported.

2. Mutation tests on the test suite itself. Each mutant deliberately breaks
   the checker in a way the Standard names - a weakened gate, an omitted
   cause, wrong ordering, altered positions - and the acceptance assertions
   must notice. A suite that still passed against a broken checker would be
   no evidence.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
from collections.abc import Callable
from typing import Any

import pytest

from planlens_agent import check, checker
from planlens_agent.diagnostics import Diagnostic

from helpers import ACCEPTANCE, TEMPLATE, load_cases
from test_acceptance import _paths, assert_case

CONTRACT, CATALOG = TEMPLATE / "contract.toml", TEMPLATE / "catalog.toml"
STEPS = json.loads((TEMPLATE / "plans" / "valid-alternate-order.json").read_text())["steps"]


def oracle(order: list[str]) -> set[str]:
    """Hand-written from the contract text, not from the checker."""
    pos = {action: i for i, action in enumerate(order)}
    found: set[str] = set()
    # unit_tests, security_scan and deploy all consume build_rc.artifact
    if any(pos["build"] > pos[a] for a in ("unit_tests", "security_scan", "deploy")):
        found.add("plan/reference-before-producer")
    # deploy needs earlier code_review, unit_tests and security_scan
    if any(pos[a] > pos["deploy"] for a in ("code_review", "unit_tests", "security_scan")):
        found.add("plan/prerequisite-order")
    return found


def test_checker_matches_independent_oracle_on_every_ordering() -> None:
    passes = 0
    for perm in itertools.permutations(STEPS):
        plan = {"schema": "planlens-agent/plan/v1", "steps": list(perm)}
        expected = oracle([s["action"] for s in perm])
        result = check(plan, CONTRACT, CATALOG)
        assert {d.rule_id for d in result.diagnostics} == expected, [s["id"] for s in perm]
        assert result.passed == (not expected)
        passes += result.passed
    # deploy last, build before both checks, review anywhere before deploy: 4!/3 = 8
    assert passes == 8


def test_every_finding_position_matches_its_path() -> None:
    _, cases = load_cases()
    for case in cases:
        for d in check(*_paths(case)).diagnostics:
            loc = d.location
            if loc is not None and loc.step_index is not None:
                assert loc.path.startswith(f"/steps/{loc.step_index}"), (case["name"], loc)


# --- mutants -------------------------------------------------------------------
Evaluate = Callable[..., list[Diagnostic]]
REAL_EVALUATE: Evaluate = checker.evaluate


def weakened_gate(*a: Any) -> list[Diagnostic]:
    return []  # never reports anything


def omitted_cause(*a: Any) -> list[Diagnostic]:
    return [d for d in REAL_EVALUATE(*a) if d.rule_id != "plan/missing-required-action"]


def wrong_ordering(plan: Any, catalog: Any, contract: Any) -> list[Diagnostic]:
    flipped = tuple(
        dataclasses.replace(s, index=len(plan.steps) - 1 - s.index) for s in reversed(plan.steps)
    )
    return REAL_EVALUATE(dataclasses.replace(plan, steps=flipped), catalog, contract)


def altered_positions(*a: Any) -> list[Diagnostic]:
    out = []
    for d in REAL_EVALUATE(*a):
        loc = d.location
        if loc is not None and loc.step_index is not None:
            n = loc.step_index + 1
            tail = loc.path.split("/", 3)[3] if loc.path.count("/") >= 3 else ""
            loc = dataclasses.replace(
                loc, step_index=n, path=f"/steps/{n}" + (f"/{tail}" if tail else "")
            )
        out.append(dataclasses.replace(d, location=loc))
    return out


@pytest.mark.parametrize(
    "mutant",
    [weakened_gate, omitted_cause, wrong_ordering, altered_positions],
    ids=lambda f: f.__name__,
)
def test_acceptance_assertions_detect_a_broken_checker(
    mutant: Evaluate, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(checker, "evaluate", mutant)
    _, cases = load_cases()
    caught = []
    for case in cases:
        try:
            assert_case(case, check(*_paths(case)))
        except AssertionError:
            caught.append(case["name"])
    assert caught, f"mutant {mutant.__name__} was not detected by any acceptance case"


def test_mutants_are_also_caught_by_template_regression_fixtures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from planlens_agent import run_fixtures

    assert run_fixtures(TEMPLATE / "fixtures.toml").all_met
    monkeypatch.setattr(checker, "evaluate", weakened_gate)
    run = run_fixtures(TEMPLATE / "fixtures.toml")
    assert not run.all_met
    assert {c.name for c in run.cases if not c.met} == {
        "deploy-before-review",
        "deploys-an-untested-build",
    }


def test_acceptance_dir_exists() -> None:
    assert (ACCEPTANCE / "cases.toml").is_file()
