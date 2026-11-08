"""Targeted tests added during the checkpoint A source review (A_REVIEW.md).

Each covers a behaviour the review asked about that no earlier test exercised:
artifact typing, self- and unknown-producer references, repeated actions, and the
exact matching scope of same_artifact. Behaviour already covered elsewhere is cited
in A_REVIEW.md rather than repeated here.
"""

from __future__ import annotations

import copy
import tomllib
from collections import Counter
from typing import Any

from planlens_agent import check

from helpers import TEMPLATE

CATALOG = tomllib.loads((TEMPLATE / "catalog.toml").read_text())
CONTRACT = tomllib.loads((TEMPLATE / "contract.toml").read_text())


def plan(*steps: dict[str, Any]) -> dict[str, Any]:
    return {"schema": "planlens-agent/plan/v1", "steps": list(steps)}


def build(sid: str = "b") -> dict[str, Any]:
    return {"id": sid, "action": "build"}


def uses(sid: str, action: str, ref: str) -> dict[str, Any]:
    return {"id": sid, "action": action, "inputs": {"artifact": ref}}


def review(sid: str = "r") -> dict[str, Any]:
    return {"id": sid, "action": "code_review"}


def findings(result: Any) -> Counter[tuple[str, str]]:
    return Counter((d.rule_id, d.location.path if d.location else "") for d in result.diagnostics)


# --- Q1: references resolve to an existing, correctly typed, earlier output -----------
def test_wrong_artifact_type_is_rejected() -> None:
    catalog = copy.deepcopy(CATALOG)
    catalog["actions"]["build_docs"] = {"outputs": {"artifact": "docs-bundle"}}
    p = plan(
        build(),
        {"id": "docs", "action": "build_docs"},
        uses("t", "unit_tests", "docs.artifact"),
        uses("s", "security_scan", "docs.artifact"),
        review(),
        uses("d", "deploy", "docs.artifact"),
    )
    r = check(p, CONTRACT, catalog)
    assert r.verdict == "reject"
    assert findings(r) == Counter(
        {
            ("plan/artifact-type", "/steps/2/inputs/artifact"): 1,
            ("plan/artifact-type", "/steps/3/inputs/artifact"): 1,
            ("plan/artifact-type", "/steps/5/inputs/artifact"): 1,
        }
    )
    d = r.diagnostics[0]
    assert (d.observed, d.required) == ("docs-bundle", "release-artifact")


def test_forward_reference_needs_no_prerequisite_rule() -> None:
    """The template contract has no rule ordering build before the checks, so the
    rejection comes from reference resolution alone."""
    assert not [
        r
        for r in CONTRACT["rules"]
        if r["kind"] == "prerequisite" and r["requires_prior"] == "build"
    ]
    p = plan(
        uses("t", "unit_tests", "b.artifact"),
        build(),
        uses("s", "security_scan", "b.artifact"),
        review(),
        uses("d", "deploy", "b.artifact"),
    )
    assert findings(check(p, CONTRACT, CATALOG)) == Counter(
        {("plan/reference-before-producer", "/steps/0/inputs/artifact"): 1}
    )


def test_self_reference_is_rejected() -> None:
    catalog = copy.deepcopy(CATALOG)
    catalog["actions"]["sign"] = {
        "inputs": {"artifact": "release-artifact"},
        "outputs": {"artifact": "release-artifact"},
    }
    p = plan(
        build(),
        uses("sg", "sign", "sg.artifact"),
        uses("t", "unit_tests", "b.artifact"),
        uses("s", "security_scan", "b.artifact"),
        review(),
        uses("d", "deploy", "b.artifact"),
    )
    r = check(p, CONTRACT, catalog)
    [d] = r.diagnostics
    assert d.rule_id == "plan/reference-before-producer"
    assert d.observed == {"consumer_index": 1, "producer_index": 1}


def test_reference_to_a_step_with_an_unknown_action_does_not_resolve() -> None:
    p = plan(
        build(),
        {"id": "x", "action": "mystery"},
        uses("t", "unit_tests", "x.artifact"),
        uses("s", "security_scan", "b.artifact"),
        review(),
        uses("d", "deploy", "b.artifact"),
    )
    assert findings(check(p, CONTRACT, CATALOG)) == Counter(
        {
            ("plan/unknown-action", "/steps/1/action"): 1,
            ("plan/unresolved-reference", "/steps/2/inputs/artifact"): 1,
        }
    )


# --- Q2: every occurrence of a repeated action is evaluated --------------------------
def test_each_occurrence_of_a_repeated_action_needs_its_own_prior_prerequisite() -> None:
    first_too_early = plan(
        build(),
        uses("t", "unit_tests", "b.artifact"),
        uses("s", "security_scan", "b.artifact"),
        uses("d1", "deploy", "b.artifact"),
        review(),
        uses("d2", "deploy", "b.artifact"),
    )
    assert findings(check(first_too_early, CONTRACT, CATALOG)) == Counter(
        {("plan/prerequisite-order", "/steps/3"): 1}
    )  # d1 only; d2 has review before it

    neither = plan(
        build(),
        uses("t", "unit_tests", "b.artifact"),
        uses("s", "security_scan", "b.artifact"),
        uses("d1", "deploy", "b.artifact"),
        uses("d2", "deploy", "b.artifact"),
        review(),
    )
    assert findings(check(neither, CONTRACT, CATALOG)) == Counter(
        {("plan/prerequisite-order", "/steps/3"): 1, ("plan/prerequisite-order", "/steps/4"): 1}
    )


def test_repeated_deploys_of_the_same_artifact_pass() -> None:
    p = plan(
        build(),
        uses("t", "unit_tests", "b.artifact"),
        uses("s", "security_scan", "b.artifact"),
        review(),
        uses("canary", "deploy", "b.artifact"),
        uses("prod", "deploy", "b.artifact"),
    )
    assert check(p, CONTRACT, CATALOG).passed


def test_a_later_occurrence_of_a_listed_action_cannot_hide_behind_the_first() -> None:
    p = plan(
        build("b1"),
        build("b2"),
        uses("t", "unit_tests", "b1.artifact"),
        uses("s", "security_scan", "b1.artifact"),
        review(),
        uses("canary", "deploy", "b1.artifact"),
        uses("prod", "deploy", "b2.artifact"),
    )
    assert findings(check(p, CONTRACT, CATALOG)) == Counter(
        {("plan/artifact-identity", "/steps/6/inputs/artifact"): 1}
    )


def test_a_second_test_run_on_another_build_is_caught() -> None:
    p = plan(
        build("b1"),
        build("b2"),
        uses("t1", "unit_tests", "b1.artifact"),
        uses("t2", "unit_tests", "b2.artifact"),
        uses("s", "security_scan", "b1.artifact"),
        review(),
        uses("d", "deploy", "b1.artifact"),
    )
    assert findings(check(p, CONTRACT, CATALOG)) == Counter(
        {("plan/artifact-identity", "/steps/3/inputs/artifact"): 1}
    )


# --- Q3: the matching scope of same_artifact -----------------------------------------
def test_three_distinct_artifacts_give_one_finding_listing_every_binding() -> None:
    p = plan(
        build("a"),
        build("bb"),
        build("c"),
        uses("t", "unit_tests", "a.artifact"),
        uses("s", "security_scan", "bb.artifact"),
        review(),
        uses("d", "deploy", "c.artifact"),
    )
    [d] = check(p, CONTRACT, CATALOG).diagnostics
    assert d.rule_id == "plan/artifact-identity"
    assert d.location is not None and d.location.path == "/steps/4/inputs/artifact"
    assert [(b["step_id"], b["refers_to"]) for b in d.observed] == [
        ("t", "a.artifact"),
        ("s", "bb.artifact"),
        ("d", "c.artifact"),
    ]


def test_an_absent_member_action_contributes_nothing() -> None:
    """Presence is required_actions' job, not same_artifact's."""
    contract = copy.deepcopy(CONTRACT)
    contract["required_actions"].remove("security_scan")
    contract["rules"] = [r for r in contract["rules"] if r["id"] != "scan-before-deploy"]
    p = plan(
        build(), uses("t", "unit_tests", "b.artifact"), review(), uses("d", "deploy", "b.artifact")
    )
    assert check(p, contract, CATALOG).passed


def test_an_unresolved_member_is_not_compared_but_still_rejects() -> None:
    p = plan(
        build(),
        uses("t", "unit_tests", "b.artifact"),
        uses("s", "security_scan", "b.artifact"),
        review(),
        uses("d", "deploy", "nowhere.artifact"),
    )
    r = check(p, CONTRACT, CATALOG)
    assert r.verdict == "reject"
    assert findings(r) == Counter({("plan/unresolved-reference", "/steps/4/inputs/artifact"): 1})
