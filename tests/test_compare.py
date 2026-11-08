"""``suite diff``: what changed, without guessing and without implying approval."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from planlens_agent import compare_runs, load_run
from planlens_agent.compare import finding_evidence, finding_identity

from helpers import TEMPLATE, run_cli

S = "planlens-agent/plan/v1"


def step(sid: str, action: str, artifact: str | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"id": sid, "action": action}
    if artifact:
        d["inputs"] = {"artifact": artifact}
    return d


BUILD = step("build_rc", "build")
TEST = step("test_rc", "unit_tests", "build_rc.artifact")
SCAN = step("scan_rc", "security_scan", "build_rc.artifact")
REVIEW = step("review", "code_review")
DEPLOY = step("deploy_prod", "deploy", "build_rc.artifact")
NOTE = step("note", "notify")
VALID = [BUILD, TEST, SCAN, REVIEW, DEPLOY]
EARLY_DEPLOY = [BUILD, TEST, SCAN, DEPLOY, REVIEW]


class Project:
    """A suite whose candidates a test writes directly, so each scenario is real."""

    def __init__(self, root: Path) -> None:
        self.root = root
        shutil.copy(TEMPLATE / "contract.toml", root)
        shutil.copy(TEMPLATE / "catalog.toml", root)
        self.count = 0

    def suite(self, names: list[str], suite_id: str = "demo") -> Path:
        path = self.root / f"{suite_id}-{len(names)}-{self.count}.toml"
        self.count += 1
        cases = "".join(f'[[cases]]\nid = "{n}"\nplan = "{n}.json"\n' for n in names)
        path.write_text(
            f'schema = "planlens-agent/suite/v1"\nid = "{suite_id}"\n'
            f'contract = "contract.toml"\ncatalog = "catalog.toml"\n{cases}'
        )
        return path

    def run(self, plans: dict[str, Any], suite_id: str = "demo", **labels: str) -> Path:
        self.count += 1
        directory = self.root / f"plans{self.count}"
        directory.mkdir()
        for name, steps in plans.items():
            text = steps if isinstance(steps, str) else json.dumps({"schema": S, "steps": steps})
            (directory / f"{name}.json").write_text(text)
        out = self.root / f"run{self.count}.run.json"
        args = [f"--label={k}={v}" for k, v in labels.items()]
        run_cli(
            "suite", "run", str(self.suite(list(plans), suite_id)), "--plans-dir", str(directory),
            "--output", str(out), *args, cwd=self.root,
        )  # fmt: skip
        return out


@pytest.fixture
def p(tmp_path: Path) -> Project:
    return Project(tmp_path)


def diff(a: Path, b: Path) -> dict[str, Any]:
    return compare_runs(load_run(a), load_run(b))


def rewrite(path: Path, change: Any) -> Path:
    doc = json.loads(path.read_text())
    change(doc)
    doc["summary"]["findings"] = sum(len(c["findings"]) for c in doc["cases"])
    path.write_text(json.dumps(doc))
    return path


def counts(d: dict[str, Any]) -> tuple[int, int, int, int, int]:
    s = d["summary"]
    return s["new"], s["resolved"], s["unchanged"], s["changed_evidence"], s["ambiguous"]


# --- the authored journey -----------------------------------------------------------
def test_the_template_journey(tmp_path: Path) -> None:
    project = tmp_path / "t"
    shutil.copytree(TEMPLATE, project)
    for name in ("v1", "v2", "v3"):
        run_cli(
            "suite", "run", "suite.toml", "--plans-dir", f"candidates/{name}",
            "--output", f"{name}.run.json", cwd=project,
        )  # fmt: skip
    d = diff(project / "v1.run.json", project / "v2.run.json")
    assert d["status"] == "new_violations"
    assert [(e["case_id"], e["contract_rule"]) for e in d["findings"]["new"]] == [
        ("expedite", "review-before-deploy")
    ]
    assert [e["case_id"] for e in d["findings"]["resolved"]] == ["hotfix"]
    assert [(e["case_id"], e["moved"]) for e in d["findings"]["unchanged"]] == [("legacy", True)]

    d = diff(project / "v2.run.json", project / "v3.run.json")
    assert d["status"] == "no_new_violations"
    assert d["candidate_passes"] is True
    assert counts(d) == (0, 2, 0, 0, 0)


# --- matching by identity -----------------------------------------------------------
def test_a_new_violation_exits_one(p: Project) -> None:
    d = diff(p.run({"a": VALID}), p.run({"a": EARLY_DEPLOY}))
    assert d["status"] == "new_violations"
    assert d["exit_code"] == 1
    assert counts(d) == (1, 0, 0, 0, 0)


def test_a_resolved_violation_is_reported_and_exits_zero(p: Project) -> None:
    d = diff(p.run({"a": EARLY_DEPLOY}), p.run({"a": VALID}))
    assert d["status"] == "no_new_violations"
    assert counts(d) == (0, 1, 0, 0, 0)


def test_moving_a_step_alone_is_not_a_resolved_and_a_new_finding(p: Project) -> None:
    d = diff(p.run({"a": EARLY_DEPLOY}), p.run({"a": [NOTE, *EARLY_DEPLOY]}))
    assert counts(d) == (0, 0, 1, 0, 0)
    entry = d["findings"]["unchanged"][0]
    assert (entry["baseline_position"], entry["position"], entry["moved"]) == (3, 4, True)
    assert d["status"] == "no_new_violations"


def test_an_index_in_observed_evidence_does_not_count_as_a_change(p: Project) -> None:
    later_build = [TEST, BUILD, SCAN, REVIEW, DEPLOY]
    d = diff(p.run({"a": later_build}), p.run({"a": [NOTE, *later_build]}))
    assert counts(d) == (0, 0, 1, 0, 0)


def test_the_list_of_plan_steps_in_evidence_does_not_count_as_a_change(p: Project) -> None:
    dangling = [BUILD, step("test_rc", "unit_tests", "ghost.artifact"), SCAN, REVIEW, DEPLOY]
    d = diff(p.run({"a": dangling}), p.run({"a": [NOTE, *dangling]}))
    assert d["summary"]["new"] == d["summary"]["resolved"] == 0
    assert d["summary"]["changed_evidence"] == 0


def test_reordering_the_steps_a_finding_lists_is_not_a_change(p: Project) -> None:
    other = step("build_other", "build")
    deploy = step("deploy_prod", "deploy", "build_other.artifact")
    a = [BUILD, other, TEST, SCAN, REVIEW, deploy]
    b = [BUILD, other, SCAN, TEST, REVIEW, deploy]
    d = diff(p.run({"a": a}), p.run({"a": b}))
    assert counts(d) == (0, 0, 1, 0, 0)


def test_the_same_requirement_failing_differently_is_changed_evidence(p: Project) -> None:
    other = step("build_other", "build")
    third = step("build_third", "build")
    a = [
        BUILD,
        other,
        third,
        TEST,
        SCAN,
        REVIEW,
        step("deploy_prod", "deploy", "build_other.artifact"),
    ]
    b = [
        BUILD,
        other,
        third,
        TEST,
        SCAN,
        REVIEW,
        step("deploy_prod", "deploy", "build_third.artifact"),
    ]
    d = diff(p.run({"a": a}), p.run({"a": b}))
    assert counts(d) == (0, 0, 0, 1, 0)
    assert d["status"] == "no_new_violations"
    changed = d["findings"]["changed_evidence"][0]
    assert changed["baseline"]["rule_id"] == changed["candidate"]["rule_id"]


def test_the_same_rule_at_another_step_is_a_different_finding(p: Project) -> None:
    second = step("deploy_again", "deploy", "build_rc.artifact")
    d = diff(p.run({"a": EARLY_DEPLOY}), p.run({"a": [BUILD, TEST, SCAN, second, REVIEW]}))
    assert counts(d) == (1, 1, 0, 0, 0)  # documented: renames are not followed


def test_identity_ignores_position_and_wording() -> None:
    def finding(path: str, index: int, message: str) -> dict[str, Any]:
        return {
            "rule_id": "plan/prerequisite-order",
            "contract_rule": "r",
            "location": {"document": "plan", "path": path, "step_id": "s", "step_index": index},
            "message": message,
            "observed": {"later_occurrences": [{"step_id": "x", "step_index": index + 1}]},
            "required": "a before b",
            "evidence": {},
            "related_locations": [],
        }

    a, b = finding("/steps/2", 2, "at steps[2]"), finding("/steps/9", 9, "at steps[9]")
    assert finding_identity(a) == finding_identity(b)
    assert finding_evidence(a) == finding_evidence(b)


def test_several_findings_with_one_identity_are_not_collapsed(p: Project) -> None:
    def spread(doc: dict[str, Any], *values: str) -> None:
        original = doc["cases"][0]["findings"][0]
        doc["cases"][0]["findings"] = [{**original, "required": v} for v in values]

    # ``required`` is part of what the finding says, so these differ only in evidence.
    a = rewrite(p.run({"a": EARLY_DEPLOY}), lambda d: spread(d, "one", "two"))
    b = rewrite(p.run({"a": EARLY_DEPLOY}), lambda d: spread(d, "three", "four"))
    d = diff(a, b)
    assert d["status"] == "incomplete_comparison"
    assert d["exit_code"] == 2
    assert counts(d) == (0, 0, 0, 0, 1)
    group = d["findings"]["ambiguous"][0]
    assert (len(group["baseline"]), len(group["candidate"])) == (2, 2)


def test_identical_duplicates_pair_one_to_one(p: Project) -> None:
    def spread(doc: dict[str, Any], *values: str) -> None:
        original = doc["cases"][0]["findings"][0]
        doc["cases"][0]["findings"] = [{**original, "required": v} for v in values]

    a = rewrite(p.run({"a": EARLY_DEPLOY}), lambda d: spread(d, "one", "one", "two"))
    b = rewrite(p.run({"a": EARLY_DEPLOY}), lambda d: spread(d, "one", "two"))
    assert counts(diff(a, b)) == (0, 1, 2, 0, 0)


# --- no regression is not a pass ----------------------------------------------------
def test_two_identical_rejected_runs_have_no_regression_and_still_fail(p: Project) -> None:
    baseline = p.run({"a": EARLY_DEPLOY})
    candidate = p.run({"a": EARLY_DEPLOY})
    d = diff(baseline, candidate)
    assert d["status"] == "no_new_violations"
    assert d["exit_code"] == 0
    assert d["no_new_violations"] is True
    assert d["approval"] is False
    assert d["candidate_passes"] is False
    assert d["candidate_status"] == "rejected"
    assert d["cases"]["still_rejected"] == [{"case_id": "a", "sample": None}]
    assert json.loads(candidate.read_text())["exit_code"] == 1  # the gate still fails

    shown = run_cli("suite", "diff", str(baseline), str(candidate))
    assert shown.returncode == 0
    assert "candidate run: rejected, 1 case(s) rejected" in shown.stdout
    assert "not an approval" in shown.stdout


def test_a_comparison_is_never_marked_as_an_approval(p: Project) -> None:
    d = diff(p.run({"a": VALID}), p.run({"a": VALID}))
    assert d["candidate_passes"] is True
    assert d["approval"] is False


# --- replay -------------------------------------------------------------------------
def test_identical_plans_are_a_replay_whatever_the_labels_say(p: Project) -> None:
    a = p.run({"a": VALID}, model="old-model")
    b = p.run({"a": VALID}, model="new-model")
    d = diff(a, b)
    assert d["replay"] is True
    assert "says nothing about a new planner, model or prompt" in d["notes"][0]
    shown = run_cli("suite", "diff", str(a), str(b)).stdout
    assert "REPLAY of identical plans" in shown
    assert "says nothing about a new planner" in shown
    assert "labels are caller-supplied and not verified" in shown


def test_changed_plans_are_not_a_replay(p: Project) -> None:
    d = diff(p.run({"a": VALID}), p.run({"a": [*VALID, NOTE]}))
    assert d["replay"] is False
    assert d["plans"] == {"changed": 1, "unchanged": 0, "unknown": 0}


def test_an_added_case_is_not_a_replay(p: Project) -> None:
    assert diff(p.run({"a": VALID}), p.run({"a": VALID, "b": VALID}))["replay"] is False


# --- every case accounted for -------------------------------------------------------
def test_dropping_a_failing_case_is_not_an_improvement(p: Project) -> None:
    d = diff(p.run({"a": VALID, "b": EARLY_DEPLOY}), p.run({"a": VALID}))
    assert d["status"] == "incomplete_comparison"
    assert d["exit_code"] == 2
    assert d["summary"]["resolved"] == 0
    assert d["cases"]["removed"] == [{"case_id": "b", "sample": None}]
    assert "a dropped case is not a resolved case" in d["incomplete_reasons"][0]


def test_dropping_a_passing_case_is_also_incomplete(p: Project) -> None:
    d = diff(p.run({"a": VALID, "b": VALID}), p.run({"a": VALID}))
    assert d["status"] == "incomplete_comparison"


def test_a_passing_added_case_is_accounted_for(p: Project) -> None:
    d = diff(p.run({"a": VALID}), p.run({"a": VALID, "b": VALID}))
    assert d["status"] == "no_new_violations"
    assert d["cases"]["added"] == [{"case_id": "b", "sample": None}]


def test_a_rejected_added_case_brings_new_findings(p: Project) -> None:
    d = diff(p.run({"a": VALID}), p.run({"a": VALID, "b": EARLY_DEPLOY}))
    assert d["status"] == "new_violations"
    assert d["findings"]["new"][0]["case_id"] == "b"


def test_a_candidate_error_is_not_a_resolved_finding(p: Project) -> None:
    d = diff(p.run({"a": EARLY_DEPLOY}), p.run({"a": "{not json"}))
    assert d["status"] == "incomplete_comparison"
    assert d["summary"]["resolved"] == 0
    assert d["cases"]["not_comparable"] == [
        {"case_id": "a", "sample": None, "baseline_verdict": "reject", "candidate_verdict": "error"}
    ]


def test_an_incomplete_baseline_is_not_comparable_case_by_case(p: Project) -> None:
    d = diff(p.run({"a": "{not json"}), p.run({"a": EARLY_DEPLOY}))
    assert d["status"] == "incomplete_comparison"
    assert d["summary"]["new"] == 0


def test_an_added_case_that_errors_is_incomplete(p: Project) -> None:
    d = diff(p.run({"a": VALID}), p.run({"a": VALID, "b": "{"}))
    assert d["status"] == "incomplete_comparison"


def test_suite_errors_make_the_comparison_incomplete(p: Project) -> None:
    def add_error(doc: dict[str, Any]) -> None:
        doc["suite_errors"] = [{"code": "invalid-case", "message": "m", "path": "/cases/1"}]
        doc["summary"]["suite_errors"] = 1
        doc["status"], doc["exit_code"] = "incomplete", 2

    d = diff(p.run({"a": VALID}), rewrite(p.run({"a": VALID}), add_error))
    assert d["status"] == "incomplete_comparison"


# --- context changes are not attributed to the plans --------------------------------
def test_a_changed_contract_is_not_comparable(p: Project) -> None:
    baseline = p.run({"a": EARLY_DEPLOY})
    contract = p.root / "contract.toml"
    contract.write_text(
        contract.read_text().replace(
            'version = "1.0.0"\ndescription', 'version = "1.1.0"\ndescription'
        )
    )
    candidate = p.run({"a": VALID})
    d = diff(baseline, candidate)
    assert d["status"] == "not_comparable"
    assert d["exit_code"] == 2
    assert [c["kind"] for c in d["context_changes"]] == ["contract"]
    assert d["no_new_violations"] is False
    assert d["summary"]["resolved"] == 1  # the delta is shown, under the warning
    shown = run_cli("suite", "diff", str(baseline), str(candidate)).stdout
    assert "NOT COMPARABLE" in shown
    assert "Do not attribute these differences to the candidate plans" in shown


def test_a_changed_catalog_is_not_comparable(p: Project) -> None:
    baseline = p.run({"a": VALID})
    catalog = p.root / "catalog.toml"
    catalog.write_text(catalog.read_text().replace("Optional; the contract", "The contract"))
    d = diff(baseline, p.run({"a": VALID}))
    assert [c["kind"] for c in d["context_changes"]] == ["catalog"]


@pytest.mark.parametrize("value", ["a" * 64, None])
def test_a_different_or_unknown_evaluator_is_not_comparable(p: Project, value: str | None) -> None:
    baseline = p.run({"a": VALID})
    candidate = rewrite(p.run({"a": VALID}), lambda d: d["engine"].update(evaluator_sha256=value))
    d = diff(baseline, candidate)
    assert d["status"] == "not_comparable"
    assert [c["kind"] for c in d["context_changes"]] == ["evaluator"]


def test_changed_rule_versions_are_not_comparable(p: Project) -> None:
    candidate = rewrite(
        p.run({"a": VALID}), lambda d: d["rules"].update({"plan/prerequisite-order": 2})
    )
    d = diff(p.run({"a": VALID}), candidate)
    assert [c["kind"] for c in d["context_changes"]] == ["rules"]


def test_a_different_suite_is_not_comparable(p: Project) -> None:
    d = diff(p.run({"a": VALID}, "first"), p.run({"a": VALID}, "second"))
    assert [c["kind"] for c in d["context_changes"]] == ["suite"]


def test_context_outranks_new_violations(p: Project) -> None:
    baseline = p.run({"a": VALID})
    candidate = rewrite(
        p.run({"a": EARLY_DEPLOY}), lambda d: d["engine"].update(evaluator_sha256="b" * 64)
    )
    assert diff(baseline, candidate)["status"] == "not_comparable"


def test_a_changed_suite_file_alone_is_noted_not_blocking(p: Project) -> None:
    d = diff(p.run({"a": VALID, "b": VALID}), p.run({"a": VALID, "b": VALID}))
    assert d["status"] == "no_new_violations"
    a = p.run({"a": VALID})
    b = rewrite(p.run({"a": VALID}), lambda doc: doc["suite"].update(sha256="c" * 64))
    d = diff(a, b)
    assert d["status"] == "no_new_violations"
    assert "The suite file changed between the runs." in d["notes"]


def test_a_python_version_difference_is_not_a_context_change(p: Project) -> None:
    b = rewrite(p.run({"a": VALID}), lambda doc: doc["runtime"].update(python="3.99.0"))
    assert diff(p.run({"a": VALID}), b)["context_changes"] == []


# --- the command --------------------------------------------------------------------
def test_an_invalid_run_exits_two_with_nothing_on_stdout(p: Project, tmp_path: Path) -> None:
    good = p.run({"a": VALID})
    bad = tmp_path / "bad.run.json"
    bad.write_text("{}")
    for args in ((str(good), str(bad)), (str(bad), str(good))):
        result = run_cli("suite", "diff", *args)
        assert result.returncode == 2
        assert result.stdout == ""
        assert "cannot compare" in result.stderr


def test_a_missing_run_exits_two(p: Project) -> None:
    result = run_cli("suite", "diff", str(p.run({"a": VALID})), "missing.run.json")
    assert result.returncode == 2
    assert "cannot read" in result.stderr


def test_diff_json_and_output(p: Project, tmp_path: Path) -> None:
    a, b = p.run({"a": VALID}), p.run({"a": EARLY_DEPLOY})
    out = tmp_path / "diff.json"
    result = run_cli("suite", "diff", str(a), str(b), "--format", "json", "--output", str(out))
    assert result.returncode == 1
    assert json.loads(result.stdout) == json.loads(out.read_text())


def test_labels_cannot_inject_terminal_control(p: Project) -> None:
    a = rewrite(p.run({"a": VALID}), lambda d: d["labels"].update(model="x\x1b[2Jy"))
    shown = run_cli("suite", "diff", str(a), str(p.run({"a": VALID}))).stdout
    assert "\x1b" not in shown


# --- an identifier both runs agree on is not thereby supported ----------------------
def test_matching_unknown_report_schemas_are_not_comparable(p: Project) -> None:
    def future(doc: dict[str, Any]) -> None:
        doc["report_schema"] = "unknown-future-schema"

    a, b = rewrite(p.run({"a": VALID}), future), rewrite(p.run({"a": VALID}), future)
    d = diff(a, b)
    assert d["status"] == "not_comparable"
    assert d["exit_code"] == 2
    assert d["no_new_violations"] is False
    assert [c["kind"] for c in d["context_changes"]] == ["report_schema"]
    assert "not supported" in d["context_changes"][0]["why"]
    shown = run_cli("suite", "diff", str(a), str(b))
    assert shown.returncode == 2
    assert "NOT COMPARABLE" in shown.stdout


def test_one_unknown_report_schema_is_not_comparable(p: Project) -> None:
    def future(doc: dict[str, Any]) -> None:
        doc["report_schema"] = "unknown-future-schema"

    d = diff(p.run({"a": VALID}), rewrite(p.run({"a": VALID}), future))
    assert d["status"] == "not_comparable"
    assert len(d["context_changes"]) == 1


def test_the_supported_report_schema_stays_comparable(p: Project) -> None:
    assert diff(p.run({"a": VALID}), p.run({"a": VALID}))["context_changes"] == []


# --- the comparison never replaces what it compares ---------------------------------
class Runs:
    def __init__(self, p: Project) -> None:
        self.baseline = p.run({"a": VALID})
        self.candidate = p.run({"a": EARLY_DEPLOY})
        self.before = {x: x.read_bytes() for x in (self.baseline, self.candidate)}

    def refused(self, output: str, cwd: Path | None = None) -> None:
        done = run_cli("suite", "diff", str(self.baseline), str(self.candidate),
                       "--output", output, cwd=cwd)  # fmt: skip
        assert done.returncode == 2, done.stdout
        assert "nothing was compared or written" in done.stderr
        assert done.stdout == ""
        for path, content in self.before.items():
            assert path.read_bytes() == content, path


@pytest.fixture
def runs(p: Project) -> Runs:
    return Runs(p)


@pytest.mark.parametrize("target", ["baseline", "candidate"])
def test_output_cannot_be_an_input(runs: Runs, target: str) -> None:
    runs.refused(str(getattr(runs, target)))


@pytest.mark.parametrize("target", ["baseline", "candidate"])
def test_output_cannot_be_an_input_by_another_spelling(runs: Runs, target: str) -> None:
    path = getattr(runs, target)
    runs.refused(path.name, cwd=path.parent)
    runs.refused(f"./{path.name}", cwd=path.parent)
    runs.refused(str(path.parent / "." / path.name))
    (path.parent / "sub").mkdir(exist_ok=True)
    runs.refused(f"sub/../{path.name}", cwd=path.parent)


def _link(kind: str, source: Path, link: Path) -> None:
    try:
        getattr(os, kind)(source, link)
    except (OSError, NotImplementedError) as exc:  # e.g. Windows without the privilege
        pytest.skip(f"{kind} is not available here: {exc}")


@pytest.mark.parametrize("target", ["baseline", "candidate"])
def test_output_cannot_be_a_symlink_to_an_input(runs: Runs, target: str, tmp_path: Path) -> None:
    link = tmp_path / f"link-{target}.json"
    _link("symlink", getattr(runs, target), link)
    runs.refused(str(link))


@pytest.mark.parametrize("target", ["baseline", "candidate"])
def test_output_cannot_be_reached_through_a_linked_directory(
    runs: Runs, target: str, tmp_path: Path
) -> None:
    path = getattr(runs, target)
    alias = tmp_path / f"alias-{target}"
    _link("symlink", path.parent, alias)
    runs.refused(str(alias / path.name))


@pytest.mark.parametrize("target", ["baseline", "candidate"])
def test_output_cannot_be_a_hard_link_to_an_input(runs: Runs, target: str, tmp_path: Path) -> None:
    link = tmp_path / f"hard-{target}.json"
    _link("link", getattr(runs, target), link)
    runs.refused(str(link))


def test_output_cannot_be_a_case_variant_on_a_case_insensitive_filesystem(runs: Runs) -> None:
    variant = runs.baseline.with_name(runs.baseline.name.upper())
    if not variant.exists():
        pytest.skip("this filesystem is case-sensitive")
    runs.refused(str(variant))


def test_an_invalid_input_is_still_protected(p: Project, tmp_path: Path) -> None:
    bad = tmp_path / "bad.run.json"
    bad.write_text("not a run")
    done = run_cli("suite", "diff", str(bad), str(p.run({"a": VALID})), "--output", str(bad))
    assert done.returncode == 2
    assert bad.read_text() == "not a run"


def test_a_separate_output_file_is_written(runs: Runs, tmp_path: Path) -> None:
    out = tmp_path / "comparison.json"
    done = run_cli("suite", "diff", str(runs.baseline), str(runs.candidate), "--output", str(out))
    assert done.returncode == 1
    assert json.loads(out.read_text())["status"] == "new_violations"
    for path, content in runs.before.items():
        assert path.read_bytes() == content


def test_a_symlink_to_another_file_is_an_ordinary_output(runs: Runs, tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text("old")
    link = tmp_path / "other-link.json"
    _link("symlink", real, link)
    done = run_cli("suite", "diff", str(runs.baseline), str(runs.candidate), "--output", str(link))
    assert done.returncode == 1
    assert json.loads(real.read_text())["diff_schema"] == "planlens-agent/run-diff/v1"


@pytest.mark.parametrize("output", ["no/such/dir/out.json", "x" * 300 + ".json"])
def test_filesystem_errors_on_the_output_are_reported_normally(runs: Runs, output: str) -> None:
    done = run_cli("suite", "diff", str(runs.baseline), str(runs.candidate), "--output", output)
    assert done.returncode == 2
    assert "cannot write comparison" in done.stderr
    assert "internal error" not in done.stderr
