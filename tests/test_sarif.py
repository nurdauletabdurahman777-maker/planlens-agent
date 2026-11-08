"""SARIF 2.1.0 export, validated against the vendored official schema."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tomllib
from pathlib import Path
from typing import Any

import pytest

from planlens_agent import RULES, __version__, check, to_sarif, to_sarif_json, validate_policy

from helpers import ACCEPTANCE, TEMPLATE, load_cases, run_cli
from test_acceptance import _paths

jsonschema = pytest.importorskip("jsonschema")

SCHEMA_PATH = Path(__file__).parent / "data" / "sarif" / "sarif-schema-2.1.0.json"
SCHEMA_SHA256 = "c3b4bb2d6093897483348925aaa73af03b3e3f4bd4ca38cef26dcb4212a2682e"
CONTRACT, CATALOG = TEMPLATE / "contract.toml", TEMPLATE / "catalog.toml"
_, CASES = load_cases()


def validator() -> Any:
    return jsonschema.Draft4Validator(json.loads(SCHEMA_PATH.read_text()))


def assert_valid(log: dict[str, Any]) -> None:
    errors = [f"{list(e.path)}: {e.message}" for e in validator().iter_errors(log)]
    assert not errors, errors[:5]


def run_of(log: dict[str, Any]) -> dict[str, Any]:
    [run] = log["runs"]
    return run


def walk(obj: Any) -> Any:
    yield obj
    if isinstance(obj, dict):
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def regions(log: dict[str, Any]) -> list[Any]:
    return [o["region"] for o in walk(log) if isinstance(o, dict) and "region" in o]


def test_vendored_schema_is_the_recorded_official_file() -> None:
    assert hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest() == SCHEMA_SHA256
    notice = (SCHEMA_PATH.parent / "NOTICE.md").read_text()
    assert SCHEMA_SHA256 in notice and "OASIS Open" in notice


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_every_acceptance_case_is_schema_valid(case: dict[str, Any]) -> None:
    assert_valid(to_sarif(check(*_paths(case)), source_root=ACCEPTANCE.parents[1]))


def test_negative_controls_the_validator_rejects_bad_logs() -> None:
    good = to_sarif(check(TEMPLATE / "plans" / "reversed-review.json", CONTRACT, CATALOG))
    for mutate in (
        lambda g: g.update(version="2.0.0"),
        lambda g: g["runs"][0]["results"][0].update(level="fatal"),
        lambda g: g["runs"][0]["tool"]["driver"].pop("name"),
        lambda g: g["runs"][0]["results"][0].pop("message"),
    ):
        bad = copy.deepcopy(good)
        mutate(bad)
        assert list(validator().iter_errors(bad))


def test_rules_carry_identity_version_and_explanation() -> None:
    run = run_of(to_sarif(check(TEMPLATE / "plans" / "reversed-review.json", CONTRACT, CATALOG)))
    driver = run["tool"]["driver"]
    assert sorted(r["id"] for r in driver["rules"] + driver["notifications"]) == sorted(RULES)
    [res] = run["results"]
    rule = driver["rules"][res["ruleIndex"]]
    assert rule["id"] == res["ruleId"] == "plan/prerequisite-order"
    assert rule["name"] == "PlanPrerequisiteOrder"
    assert rule["properties"]["rule_version"] == RULES[res["ruleId"]].version
    assert rule["fullDescription"]["text"] == RULES[res["ruleId"]].summary
    assert res["properties"]["contract_rule"] == "review-before-deploy"
    assert res["properties"]["rule_version"] == 1
    assert res["properties"]["suggestion"]


def test_engine_version_is_not_presented_as_semver() -> None:
    driver = run_of(to_sarif(check(TEMPLATE / "plans" / "valid.json", CONTRACT, CATALOG)))["tool"][
        "driver"
    ]
    assert driver["version"] == __version__ and "semanticVersion" not in driver


@pytest.mark.parametrize(
    "plan_name, ok, code", [("valid.json", True, 0), ("artifact-mismatch.json", True, 1)]
)
def test_completed_analyses(plan_name: str, ok: bool, code: int) -> None:
    run = run_of(to_sarif(check(TEMPLATE / "plans" / plan_name, CONTRACT, CATALOG)))
    [inv] = run["invocations"]
    assert inv["executionSuccessful"] is ok and inv["exitCode"] == code
    assert inv["properties"]["analysis"].startswith("completed")
    assert all(r["kind"] == "fail" and r["level"] == "error" for r in run["results"])


def test_invalid_candidate_is_an_incomplete_analysis() -> None:
    run = run_of(to_sarif(check(ACCEPTANCE / "plans" / "malformed.json", CONTRACT, CATALOG)))
    [inv] = run["invocations"]
    assert inv["executionSuccessful"] is False and inv["exitCode"] == 2
    assert inv["properties"]["analysis"].startswith("incomplete")
    assert inv["properties"]["plan_evaluated"] is False


def test_unsupported_required_rule_is_open_not_a_pass_or_a_failure() -> None:
    log = to_sarif(
        check(
            TEMPLATE / "plans" / "valid.json",
            ACCEPTANCE / "contracts" / "unsupported-rule.toml",
            CATALOG,
        )
    )
    run = run_of(log)
    [res] = run["results"]
    assert (res["kind"], res["level"]) == ("open", "none")
    assert run["invocations"][0]["executionSuccessful"] is False


def test_invalid_policy_is_a_configuration_notification() -> None:
    run = run_of(
        to_sarif(
            check(
                TEMPLATE / "plans" / "valid.json", ACCEPTANCE / "contracts" / "cyclic.toml", CATALOG
            )
        )
    )
    assert run["results"] == []  # nothing is claimed about the plan
    [inv] = run["invocations"]
    [note] = inv["toolConfigurationNotifications"]
    assert inv["executionSuccessful"] is False and note["level"] == "error"
    descriptors = run["tool"]["driver"]["notifications"]
    assert (
        descriptors[note["descriptor"]["index"]]["id"]
        == note["descriptor"]["id"]
        == "contract/cycle"
    )


def test_logical_paths_kept_and_step_positions_never_become_lines() -> None:
    log = to_sarif(
        check(TEMPLATE / "plans" / "artifact-mismatch.json", CONTRACT, CATALOG),
        source_root=TEMPLATE,
    )
    [res] = run_of(log)["results"]
    [loc] = res["locations"]
    assert "region" not in loc["physicalLocation"] and regions(log) == []
    logical = loc["logicalLocations"][0]
    assert logical["fullyQualifiedName"] == "plan:/steps/5/inputs/artifact"
    assert logical["name"] == "deploy_prod" and logical["properties"]["step_index"] == 5
    assert {r["logicalLocations"][0]["fullyQualifiedName"] for r in res["relatedLocations"]} == {
        "plan:/steps/2/inputs/artifact",
        "plan:/steps/3/inputs/artifact",
        "contract:/rules/3",
    }


def test_real_parser_positions_become_regions() -> None:
    log = to_sarif(
        check(ACCEPTANCE / "plans" / "malformed.json", CONTRACT, CATALOG), source_root=ACCEPTANCE
    )
    assert regions(log) == [{"startLine": 5, "startColumn": 46}]


def test_syntax_error_in_a_policy_file_keeps_its_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{\n  nope\n}\n")
    log = to_sarif(check(TEMPLATE / "plans" / "valid.json", bad, CATALOG), source_root=tmp_path)
    assert_valid(log)
    [note] = run_of(log)["invocations"][0]["toolConfigurationNotifications"]
    physical = note["locations"][0]["physicalLocation"]
    assert physical["artifactLocation"]["uri"] == "bad.json"
    assert physical["region"] == {"startLine": 2, "startColumn": 3}


def test_missing_step_has_no_invented_location() -> None:
    log = to_sarif(check(ACCEPTANCE / "plans" / "compound.json", CONTRACT, CATALOG))
    res = next(r for r in run_of(log)["results"] if r["ruleId"] == "plan/missing-required-action")
    assert "locations" not in res
    assert res["properties"]["location_absent_reason"]
    assert res["relatedLocations"][0]["logicalLocations"][0]["fullyQualifiedName"].startswith(
        "contract:/required_actions/"
    )


def test_mapping_documents_have_logical_locations_only() -> None:
    plan = json.loads((TEMPLATE / "plans" / "reversed-review.json").read_text())
    log = to_sarif(
        check(plan, tomllib.loads(CONTRACT.read_text()), tomllib.loads(CATALOG.read_text())),
        source_root=TEMPLATE,
    )
    assert_valid(log)
    run = run_of(log)
    assert "artifacts" not in run and "originalUriBaseIds" not in run
    assert not [o for o in walk(log) if isinstance(o, dict) and "physicalLocation" in o]
    assert (
        run["results"][0]["locations"][0]["logicalLocations"][0]["fullyQualifiedName"]
        == "plan:/steps/3"
    )


def test_paths_with_spaces_and_unicode_are_uri_encoded_and_relative(tmp_path: Path) -> None:
    folder = tmp_path / "my plans" / "релиз"
    folder.mkdir(parents=True)
    plan = folder / "plan #1.json"
    plan.write_text((TEMPLATE / "plans" / "reversed-review.json").read_text())
    log = to_sarif(check(plan, CONTRACT, CATALOG), source_root=tmp_path)
    assert_valid(log)
    [res] = run_of(log)["results"]
    art = res["locations"][0]["physicalLocation"]["artifactLocation"]
    assert art["uri"] == "my%20plans/%D1%80%D0%B5%D0%BB%D0%B8%D0%B7/plan%20%231.json"
    assert art["uriBaseId"] == "SRCROOT"
    assert run_of(log)["artifacts"][art["index"]]["location"]["uri"] == art["uri"]


def test_absolute_source_root_is_not_written_into_the_log(tmp_path: Path) -> None:
    plan = tmp_path / "p.json"
    plan.write_text((TEMPLATE / "plans" / "reversed-review.json").read_text())
    (tmp_path / "contract.toml").write_text(CONTRACT.read_text())
    (tmp_path / "catalog.toml").write_text(CATALOG.read_text())
    text = to_sarif_json(
        check(plan, tmp_path / "contract.toml", tmp_path / "catalog.toml"), source_root=tmp_path
    )
    assert str(tmp_path) not in text
    base = run_of(json.loads(text))["originalUriBaseIds"]["SRCROOT"]
    assert "uri" not in base and base["description"]["text"]


def test_files_outside_the_source_root_get_absolute_file_uris(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    log = to_sarif(
        check(TEMPLATE / "plans" / "reversed-review.json", CONTRACT, CATALOG), source_root=root
    )
    assert_valid(log)
    art = run_of(log)["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"]
    assert art["uri"].startswith("file:///") and "uriBaseId" not in art


# --- source locations are fixed when a document is read -------------------------------------
@pytest.fixture
def two_dirs(tmp_path: Path) -> tuple[Path, Path]:
    """Directories a and b, each with its own plan, contract and catalog."""
    a, b = tmp_path / "a", tmp_path / "b"
    for folder, title in ((a, "from a"), (b, "from b")):
        folder.mkdir()
        doc = json.loads((TEMPLATE / "plans" / "reversed-review.json").read_text())
        doc["title"] = title
        (folder / "plan.json").write_text(json.dumps(doc))
        (folder / "contract.toml").write_text(CONTRACT.read_text())
        (folder / "catalog.toml").write_text(CATALOG.read_text())
    return a, b


def uris(log: dict[str, Any]) -> list[str]:
    return [a["location"]["uri"] for a in run_of(log)["artifacts"]]


def test_reviewer_reproduction_export_after_cwd_change(
    two_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = two_dirs
    monkeypatch.chdir(a)
    result = check("plan.json", CONTRACT.resolve(), CATALOG.resolve())
    monkeypatch.chdir(b)
    art = run_of(to_sarif(result, source_root=a))["results"][0]["locations"][0]["physicalLocation"][
        "artifactLocation"
    ]
    assert (art["uri"], art.get("uriBaseId")) == ("plan.json", "SRCROOT")


def test_all_three_documents_keep_their_load_time_location(
    two_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = two_dirs
    monkeypatch.chdir(a)
    result = check("plan.json", "contract.toml", "catalog.toml")
    expected = ["plan.json", "contract.toml", "catalog.toml"]
    assert uris(to_sarif(result, source_root=a)) == expected
    monkeypatch.chdir(b)
    log = to_sarif(result, source_root=a)
    assert uris(log) == expected
    assert_valid(log)


def test_a_reused_policy_keeps_its_own_location(
    two_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = two_dirs
    monkeypatch.chdir(a)
    policy = validate_policy("contract.toml", "catalog.toml")
    monkeypatch.chdir(b)
    result = check("plan.json", policy=policy)
    assert sorted(uris(to_sarif(result, source_root=a.parent))) == [
        "a/catalog.toml",
        "a/contract.toml",
        "b/plan.json",
    ]


def test_export_output_does_not_depend_on_the_working_directory(
    two_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = two_dirs
    monkeypatch.chdir(a)
    result = check("plan.json", "contract.toml", "catalog.toml")
    first = to_sarif_json(result, source_root=a)
    monkeypatch.chdir(b)
    assert to_sarif_json(result, source_root=a) == first


def test_human_readable_origin_and_reports_are_unchanged(
    two_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    a, _ = two_dirs
    monkeypatch.chdir(a)
    result = check("plan.json", "contract.toml", "catalog.toml")
    report = result.to_dict()
    assert report["inputs"]["plan"]["origin"] == "plan.json"
    assert report["inputs"]["contract"]["origin"] == "contract.toml"
    assert str(a) not in result.to_json() and str(a) not in result.render_text()
    assert result.plan_source == str((a / "plan.json").resolve())


def test_a_recorded_location_is_not_a_claim_that_the_file_is_unchanged(
    two_dirs: tuple[Path, Path],
) -> None:
    a, _ = two_dirs
    result = check(a / "plan.json", a / "contract.toml", a / "catalog.toml")
    before = to_sarif(result, source_root=a)
    (a / "plan.json").write_text("{}")  # changed after the check
    after = to_sarif(result, source_root=a)
    assert after == before
    assert run_of(after)["properties"]["plan"]["canonical_sha256"] == result.plan_digest
    assert "do not show that the file is unchanged" in run_of(after)["properties"]["location_note"]
    (a / "plan.json").unlink()  # and even if it is gone
    assert to_sarif(result, source_root=a) == before


def test_a_mapping_never_gets_a_file_location(
    two_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    a, _ = two_dirs
    monkeypatch.chdir(a)
    if os.name != "nt":  # Windows does not allow < or > in a file name
        (a / "<plan mapping>").write_text("{}")  # a file named like the origin
    plan = json.loads((a / "plan.json").read_text())
    result = check(plan, tomllib.loads(CONTRACT.read_text()), tomllib.loads(CATALOG.read_text()))
    assert result.plan_source is None
    assert result.policy.contract_source is None and result.policy.catalog_source is None
    log = to_sarif(result, source_root=a)
    assert not [o for o in walk(log) if isinstance(o, dict) and "physicalLocation" in o]
    assert "artifacts" not in run_of(log)


def test_a_file_that_did_not_exist_gets_no_location(
    two_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = two_dirs
    monkeypatch.chdir(a)
    result = check("missing.json", "contract.toml", "catalog.toml")
    (b / "missing.json").write_text("{}")  # exists at export time, elsewhere
    monkeypatch.chdir(b)
    assert result.plan_source is None
    assert "missing.json" not in uris(to_sarif(result, source_root=b))


def test_policy_syntax_error_location_survives_a_cwd_change(
    two_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = two_dirs
    (a / "broken.json").write_text("{\n  nope\n}\n")
    monkeypatch.chdir(a)
    result = check("plan.json", "broken.json", "catalog.toml")
    monkeypatch.chdir(b)
    [note] = run_of(to_sarif(result, source_root=a))["invocations"][0][
        "toolConfigurationNotifications"
    ]
    physical = note["locations"][0]["physicalLocation"]
    assert physical["artifactLocation"]["uri"] == "broken.json"
    assert physical["region"] == {"startLine": 2, "startColumn": 3}


def test_fingerprints_are_stable_and_distinguish_findings() -> None:
    def prints() -> list[str]:
        log = to_sarif(check(ACCEPTANCE / "plans" / "compound.json", CONTRACT, CATALOG))
        return [r["partialFingerprints"]["planlensFinding/v1"] for r in run_of(log)["results"]]

    assert prints() == prints() and len(set(prints())) == 4


# --- CLI ------------------------------------------------------------------------------------
@pytest.fixture
def project(tmp_path: Path) -> Path:
    target = tmp_path / "my release project"
    assert run_cli("init", "release-basic", str(target)).returncode == 0
    return target


@pytest.mark.parametrize(
    "name, code", [("valid.json", 0), ("reversed-review.json", 1), ("does-not-exist.json", 2)]
)
def test_cli_sarif_keeps_the_verdict_exit_codes(project: Path, name: str, code: int) -> None:
    proc = run_cli("check", f"plans/{name}", "--format", "sarif", cwd=project)
    assert proc.returncode == code and proc.stderr == ""
    assert proc.stdout.isascii() and "\x1b" not in proc.stdout
    assert_valid(json.loads(proc.stdout))


def test_cli_sarif_output_file_matches_stdout(project: Path) -> None:
    proc = run_cli(
        "check",
        "plans/artifact-mismatch.json",
        "--format",
        "sarif",
        "--output",
        "out file.sarif",
        cwd=project,
    )
    assert (project / "out file.sarif").read_text() == proc.stdout


def test_text_and_json_output_files_are_still_the_json_report(project: Path) -> None:
    for fmt in ("text", "json"):
        run_cli("check", "plans/valid.json", "--format", fmt, "--output", "r.json", cwd=project)
        assert (
            json.loads((project / "r.json").read_text())["report_schema"]
            == "planlens-agent/report/v1"
        )


def test_cli_source_root_mapping(project: Path) -> None:
    proc = run_cli(
        "check",
        "my release project/plans/reversed-review.json",
        "--contract",
        "my release project/contract.toml",
        "--catalog",
        "my release project/catalog.toml",
        "--format",
        "sarif",
        "--source-root",
        "my release project",
        cwd=project.parent,
    )
    res = run_of(json.loads(proc.stdout))["results"][0]
    art = res["locations"][0]["physicalLocation"]["artifactLocation"]
    assert art["uri"] == "plans/reversed-review.json" and art["uriBaseId"] == "SRCROOT"


def test_cli_bad_source_root_is_a_usage_error(project: Path) -> None:
    proc = run_cli(
        "check", "plans/valid.json", "--format", "sarif", "--source-root", "nope", cwd=project
    )
    assert proc.returncode == 2 and proc.stdout == "" and "not a directory" in proc.stderr
