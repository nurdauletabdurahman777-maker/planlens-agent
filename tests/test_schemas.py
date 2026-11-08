"""The shipped JSON Schemas agree with the engine where it matters for safety.

The engine's own validation is authoritative; the schema files describe the
formats for other tools. The direction that must hold is: anything the engine
lets pass satisfies the schema, and anything the schema rejects is not passed.
"""

from __future__ import annotations

import json
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

jsonschema = pytest.importorskip("jsonschema")

from planlens_agent import (  # noqa: E402
    check,
    compare_runs,
    load_run,
    load_suite,
    run_fixtures,
    run_suite,
)

from helpers import ACCEPTANCE, TEMPLATE, load_cases  # noqa: E402
from test_acceptance import _paths  # noqa: E402


def schema(name: str) -> dict[str, Any]:
    text = (
        resources.files("planlens_agent").joinpath("schemas", f"{name}.v1.schema.json").read_text()
    )
    return json.loads(text)


def valid(name: str, doc: Any) -> bool:
    return not list(jsonschema.Draft202012Validator(schema(name)).iter_errors(doc))


def test_schemas_are_themselves_valid() -> None:
    for name in ("plan", "contract", "catalog", "report", "fixtures", "suite", "run", "run-diff"):
        jsonschema.Draft202012Validator.check_schema(schema(name))


def test_template_documents_match_their_schemas() -> None:
    assert valid("catalog", tomllib.loads((TEMPLATE / "catalog.toml").read_text()))
    assert valid("contract", tomllib.loads((TEMPLATE / "contract.toml").read_text()))
    assert valid("fixtures", tomllib.loads((TEMPLATE / "fixtures.toml").read_text()))
    for p in (TEMPLATE / "plans").glob("*.json"):
        assert valid("plan", json.loads(p.read_text())), p.name


def test_suite_runs_and_comparisons_match_their_schemas(tmp_path: Path) -> None:
    assert valid("suite", tomllib.loads((TEMPLATE / "suite.toml").read_text()))
    (tmp_path / "broken.json").write_text("{")
    suite = load_suite(TEMPLATE / "suite.toml")
    runs = {name: run_suite(suite, TEMPLATE / "candidates" / name) for name in ("v1", "v2", "v3")}
    runs["empty"] = run_suite(suite, tmp_path)  # every plan missing: an incomplete run
    files = {}
    for name, run in runs.items():
        assert valid("run", run), name
        path = tmp_path / f"{name}.run.json"
        path.write_text(json.dumps(run))
        files[name] = load_run(path)
    for a in files:
        for b in files:
            assert valid("run-diff", compare_runs(files[a], files[b])), (a, b)


def test_every_report_matches_the_report_schema() -> None:
    _, cases = load_cases()
    for case in cases:
        assert valid("report", check(*_paths(case)).to_dict()), case["name"]


def test_engine_never_passes_a_plan_the_schema_rejects() -> None:
    _, cases = load_cases()
    plans = list((ACCEPTANCE / "plans").glob("*.json")) + list((TEMPLATE / "plans").glob("*.json"))
    contract, catalog = TEMPLATE / "contract.toml", TEMPLATE / "catalog.toml"
    seen = 0
    for p in plans:
        try:
            doc = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        result = check(p, contract, catalog)
        if result.passed:
            assert valid("plan", doc), p.name
        if not valid("plan", doc):
            assert not result.passed, p.name
            seen += 1
    assert seen == 2  # policy-override.json and unsupported-semantics.json


VARIANTS = {
    "unknown step field": lambda p: p["steps"][1].update(colour="blue"),
    "reference without output": lambda p: p["steps"][1]["inputs"].update(artifact="build_rc"),
    "non-ascii step id": lambda p: p["steps"][1].update(id="тест"),
    "missing action": lambda p: p["steps"][1].pop("action"),
    "parallel mode": lambda p: p.update(mode="parallel"),
    "candidate dependency": lambda p: p["steps"][4].update(depends_on=["review"]),
    "waiver": lambda p: p.update(waive=["review-before-deploy"]),
    "self-approval": lambda p: p["steps"][4].update(approved=True),
    "declared outputs": lambda p: p["steps"][0].update(outputs={"artifact": "x"}),
    "empty steps": lambda p: p.update(steps=[]),
    "wrong schema id": lambda p: p.update(schema="planlens-agent/plan/v2"),
}


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_schema_invalid_variants_of_a_passing_plan_never_pass(name: str) -> None:
    import copy

    base = json.loads((TEMPLATE / "plans" / "valid.json").read_text())
    assert (
        valid("plan", base)
        and check(base, TEMPLATE / "contract.toml", TEMPLATE / "catalog.toml").passed
    )
    doc = copy.deepcopy(base)
    VARIANTS[name](doc)
    assert not valid("plan", doc)
    assert not check(doc, TEMPLATE / "contract.toml", TEMPLATE / "catalog.toml").passed


def test_fixture_file_matches_schema_and_runs() -> None:
    assert run_fixtures(TEMPLATE / "fixtures.toml").all_met
    assert Path(TEMPLATE / "fixtures.toml").is_file()
