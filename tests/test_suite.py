"""``suite run``: every case goes through check(), and nothing is dropped quietly."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from planlens_agent import (
    DispatchBlocked,
    ReferenceRunner,
    RunFileError,
    SuiteFileError,
    check,
    load_run,
    load_suite,
    run_suite,
)
from planlens_agent import suite as suite_module
from planlens_agent.compare import compare_runs, render_diff
from planlens_agent.suite import MAX_RUN_BYTES, MAX_RUN_DEPTH, evaluator_digest

from helpers import TEMPLATE, run_cli


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A private copy of the authored example, safe to edit."""
    target = tmp_path / "project"
    shutil.copytree(TEMPLATE, target)
    return target


def write_suite(project: Path, cases: str, head: str = "") -> Path:
    path = project / "edited.toml"
    path.write_text(
        'schema = "planlens-agent/suite/v1"\nid = "edited"\ncontract = "contract.toml"\n'
        f'catalog = "catalog.toml"\nplans_dir = "candidates/v3"\n{head}\n{cases}\n'
    )
    return path


def case(cid: str, plan: str, sample: str | None = None) -> str:
    extra = f'sample = "{sample}"\n' if sample else ""
    return f'[[cases]]\nid = "{cid}"\n{extra}plan = "{plan}"\n'


def saved(project: Path, *args: str, name: str = "out.run.json") -> tuple[Any, dict[str, Any]]:
    out = project / name
    result = run_cli("suite", "run", *args, "--output", str(out), cwd=project)
    return result, json.loads(out.read_text())


# --- exit status --------------------------------------------------------------------
def test_every_case_passing_exits_zero(project: Path) -> None:
    result, run = saved(project, "suite.toml", "--plans-dir", "candidates/v3")
    assert result.returncode == 0, result.stderr
    assert run["status"] == "passed"
    assert run["summary"]["passed"] == run["summary"]["cases"] == 5
    assert "RESULT: PASSED (exit 0)" in result.stdout


def test_a_rejected_case_exits_one_and_keeps_the_others(project: Path) -> None:
    result, run = saved(project, "suite.toml")
    assert result.returncode == 1
    assert run["status"] == "rejected"
    verdicts = {(c["case_id"], c["sample"]): c["verdict"] for c in run["cases"]}
    assert verdicts == {
        ("standard", "1"): "pass",
        ("standard", "2"): "pass",
        ("hotfix", None): "reject",
        ("expedite", None): "pass",
        ("legacy", None): "reject",
    }


def test_samples_of_one_case_are_kept_separately(project: Path) -> None:
    _, run = saved(project, "suite.toml")
    standard = [c for c in run["cases"] if c["case_id"] == "standard"]
    assert [c["sample"] for c in standard] == ["1", "2"]
    assert standard[0]["plan"]["sha256"] != standard[1]["plan"]["sha256"]


@pytest.mark.parametrize(
    ("content", "kind"),
    [
        (None, "plan/unreadable"),
        ("{not json", "plan/malformed"),
        ('{"schema": "planlens-agent/plan/v1"}', "plan/schema"),
    ],
)
def test_an_input_error_keeps_valid_results_and_marks_the_run_incomplete(
    project: Path, content: str | None, kind: str
) -> None:
    if content is not None:
        (project / "candidates/v3/broken.json").write_text(content)
    path = write_suite(project, case("good", "hotfix.json") + case("bad", "broken.json"))
    result, run = saved(project, str(path))
    assert result.returncode == 2
    assert run["status"] == "incomplete"
    good, bad = run["cases"]
    assert good["verdict"] == "pass"
    assert bad["verdict"] == "error"
    assert bad["findings"][0]["rule_id"] == kind
    assert run["summary"]["incomplete"] == 1
    assert "INCOMPLETE" in result.stdout


def test_a_rejection_does_not_hide_that_another_case_was_not_evaluated(project: Path) -> None:
    (project / "candidates/v1/broken.json").write_text("{")
    path = write_suite(project, case("rejected", "hotfix.json") + case("bad", "broken.json"), "")
    text = path.read_text().replace("candidates/v3", "candidates/v1")
    path.write_text(text)
    result, run = saved(project, str(path))
    assert result.returncode == 2
    assert run["summary"]["rejected"] == 1
    assert run["status"] == "incomplete"


def test_duplicate_case_ids_are_reported_and_neither_entry_is_evaluated(project: Path) -> None:
    path = write_suite(
        project,
        case("twin", "hotfix.json") + case("twin", "legacy.json") + case("other", "legacy.json"),
    )
    result, run = saved(project, str(path))
    assert result.returncode == 2
    assert [c["case_id"] for c in run["cases"]] == ["other"]
    assert [e["code"] for e in run["suite_errors"]] == ["duplicate-case"]
    assert "appears 2 times" in result.stdout


def test_the_same_id_with_different_samples_is_not_a_duplicate(project: Path) -> None:
    path = write_suite(project, case("twin", "hotfix.json", "a") + case("twin", "legacy.json", "b"))
    result, run = saved(project, str(path))
    assert result.returncode == 0
    assert run["suite_errors"] == []


def test_a_malformed_entry_is_recorded_and_other_cases_still_run(project: Path) -> None:
    path = write_suite(
        project,
        case("good", "hotfix.json") + '[[cases]]\nid = "bad id!"\nplan = "x.json"\n'
        '[[cases]]\nid = "extra"\nplan = "x.json"\nwaive = true\n',
    )
    result, run = saved(project, str(path))
    assert result.returncode == 2
    assert [c["case_id"] for c in run["cases"]] == ["good"]
    assert [e["code"] for e in run["suite_errors"]] == ["invalid-case", "invalid-case"]


def test_a_missing_plans_directory_is_incomplete_not_empty(project: Path) -> None:
    result, run = saved(project, "suite.toml", "--plans-dir", "no/such/dir")
    assert result.returncode == 2
    assert run["summary"]["incomplete"] == 5


def test_an_unusable_policy_marks_every_case_and_records_no_identity(project: Path) -> None:
    (project / "contract.toml").unlink()
    result, run = saved(project, "suite.toml")
    assert result.returncode == 2
    assert run["policy"]["contract"] is None
    assert {c["verdict"] for c in run["cases"]} == {"error"}
    load_run(project / "out.run.json")  # still a valid record


def test_an_over_long_plan_name_is_a_case_error(project: Path) -> None:
    path = write_suite(project, case("good", "hotfix.json") + case("long", "x" * 300 + ".json"))
    result, run = saved(project, str(path))
    assert result.returncode == 2
    assert run["cases"][1]["verdict"] == "error"
    assert "internal error" not in result.stderr


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, "not found"),
        ("not = [toml", "suite"),
        ('schema = "other"', "'schema' must be"),
        (
            'schema = "planlens-agent/suite/v1"\nid = "x"\ncontract = "c"\ncatalog = "k"\n'
            "cases = []",
            "non-empty",
        ),
        (
            'schema = "planlens-agent/suite/v1"\nid = "x"\ncontract = "c"\ncatalog = "k"\n'
            'waive = true\n[[cases]]\nid = "a"\nplan = "b"',
            "unknown fields",
        ),
        (
            'schema = "planlens-agent/suite/v1"\nid = "Not Valid"\ncontract = "c"\ncatalog = "k"',
            "'id' must match",
        ),
    ],
)
def test_an_unusable_suite_file_runs_nothing(
    project: Path, text: str | None, expected: str
) -> None:
    path = project / "bad.toml"
    if text is not None:
        path.write_text(text)
    out = project / "out.run.json"
    result = run_cli("suite", "run", "bad.toml", "--output", str(out), cwd=project)
    assert result.returncode == 2
    assert expected in result.stderr
    assert result.stdout == ""
    assert not out.exists()


def test_too_many_cases_is_refused(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(suite_module, "MAX_CASES", 2)
    path = write_suite(project, "".join(case(f"c{i}", "hotfix.json") for i in range(3)))
    with pytest.raises(SuiteFileError, match="limit"):
        load_suite(path)


# --- what a run records -------------------------------------------------------------
def test_findings_are_exactly_what_check_reports(project: Path) -> None:
    _, run = saved(project, "suite.toml")
    for c in run["cases"]:
        direct = check(
            project / "candidates/v1" / c["plan"]["path"],
            project / "contract.toml",
            project / "catalog.toml",
        )
        assert c["verdict"] == direct.verdict
        assert c["findings"] == direct.to_dict()["diagnostics"]
        assert c["plan"]["sha256"] == direct.plan_digest


def test_a_run_records_what_it_needs_to_be_audited(project: Path) -> None:
    _, run = saved(project, "suite.toml")
    assert run["run_schema"] == "planlens-agent/run/v1"
    assert run["report_schema"] == "planlens-agent/report/v1"
    assert run["engine"]["evaluator_sha256"] == evaluator_digest()
    assert (
        run["policy"]["contract"]["sha256"]
        == check(
            project / "candidates/v1/hotfix.json",
            project / "contract.toml",
            project / "catalog.toml",
        ).policy.contract.digest
    )
    assert run["rules"]["plan/prerequisite-order"] == 1
    plan = json.loads(run["cases"][0]["plan"]["canonical"])
    assert plan == json.loads((project / "candidates/v1/standard-1.json").read_text())


def test_the_evaluator_digest_tells_builds_with_one_version_apart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = evaluator_digest()
    assert current is not None
    monkeypatch.setattr(suite_module, "EVALUATOR_MODULES", suite_module.EVALUATOR_MODULES[:-1])
    assert evaluator_digest() != current


def test_an_unknown_evaluator_is_recorded_as_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(suite_module, "EVALUATOR_MODULES", ("no-such-module.py",))
    assert evaluator_digest() is None


def test_a_run_has_no_clock_so_repeating_it_is_identical(project: Path) -> None:
    saved(project, "suite.toml", name="a.run.json")
    saved(project, "suite.toml", name="b.run.json")
    assert (project / "a.run.json").read_bytes() == (project / "b.run.json").read_bytes()


def test_labels_are_kept_and_marked_unverified(project: Path) -> None:
    result, run = saved(project, "suite.toml", "--label", "model=a=b", "--label", "prompt=v2")
    assert run["labels"] == {"model": "a=b", "prompt": "v2"}
    assert "not verified" in run["labels_note"]
    assert "not verified" in result.stdout


def test_suite_labels_merge_under_command_line_labels(project: Path) -> None:
    path = write_suite(
        project, case("a", "hotfix.json"), '[labels]\nmodel = "file"\nprompt = "file"\n'
    )
    # A TOML table swallows what follows it, so the cases must come first in real files.
    path.write_text(
        path.read_text().split("[labels]")[0]
        + case("a", "hotfix.json")
        + '[labels]\nmodel = "file"\nprompt = "file"\n'
    )
    _, run = saved(project, str(path), "--label", "model=cli")
    assert run["labels"] == {"model": "cli", "prompt": "file"}


@pytest.mark.parametrize("label", ["nosign", "Bad Name=x", f"model={'x' * 201}"])
def test_a_bad_label_is_a_usage_error(project: Path, label: str) -> None:
    result = run_cli("suite", "run", "suite.toml", "--label", label, cwd=project)
    assert result.returncode == 2
    assert "invalid label" in result.stderr
    assert result.stdout == ""


def test_json_output_is_the_run_and_nothing_else(project: Path) -> None:
    result = run_cli("suite", "run", "suite.toml", "--format", "json", cwd=project)
    assert json.loads(result.stdout)["status"] == "rejected"


# --- baselines are not overwritten --------------------------------------------------
def test_an_existing_run_is_not_overwritten(project: Path) -> None:
    out = project / "baseline.run.json"
    out.write_text("reviewed")
    result = run_cli("suite", "run", "suite.toml", "--output", str(out), cwd=project)
    assert result.returncode == 2
    assert "already exists" in result.stderr
    assert out.read_text() == "reviewed"


def test_overwriting_is_explicit(project: Path) -> None:
    out = project / "baseline.run.json"
    out.write_text("old")
    result = run_cli("suite", "run", "suite.toml", "--output", str(out), "--overwrite", cwd=project)
    assert result.returncode == 1
    assert json.loads(out.read_text())["status"] == "rejected"


# --- a saved run is input, and never an approval ------------------------------------
def edited(project: Path, change: Any) -> Path:
    source = project / "good.run.json"
    if not source.exists():
        saved(project, "suite.toml", name="good.run.json")
    doc = json.loads(source.read_text())
    change(doc)
    path = project / "edited.run.json"
    path.write_text(json.dumps(doc))
    return path


def _set(*steps: Any) -> Any:
    def change(doc: dict[str, Any]) -> None:
        target: Any = doc
        for key in steps[:-2]:
            target = target[key]
        target[steps[-2]] = steps[-1]

    return change


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (_set("run_schema", "planlens-agent/run/v9"), "this engine reads"),
        (_set("extra", 1), "unknown fields"),
        (lambda d: d.pop("policy"), "missing"),
        (_set("status", "passed"), "'status' says"),
        (_set("exit_code", 0), "'exit_code'"),
        (_set("summary", "passed", 5), "'summary'"),
        (_set("cases", 0, "verdict", "maybe"), "verdict is invalid"),
        (_set("cases", 2, "exit_code", 0), "does not match verdict"),
        (_set("cases", 0, "plan", "canonical", '{"steps":[]}'), "does not match its digest"),
        (_set("cases", 0, "plan", "sha256", "0" * 64), "does not match its digest"),
        (_set("cases", 0, "plan", "sha256", "short"), "SHA-256"),
        (_set("cases", 2, "findings", []), "must record why"),
        (_set("cases", 2, "verdict", "pass"), "does not match verdict"),
        (_set("cases", 2, "findings", 0, "location", "nowhere"), "must be an object"),
        (_set("labels", "Bad Name", "x"), "label name"),
        (_set("engine", "evaluator_sha256", "nope"), "SHA-256"),
        (lambda d: d["cases"].append(d["cases"][0]), "repeats case"),
    ],
)
def test_a_damaged_run_is_refused(project: Path, change: Any, message: str) -> None:
    with pytest.raises(RunFileError, match=message):
        load_run(edited(project, change))


def test_flipping_a_rejected_case_to_pass_needs_more_than_one_edit(project: Path) -> None:
    def flip(doc: dict[str, Any]) -> None:
        doc["cases"][2].update(verdict="pass", exit_code=0)

    with pytest.raises(RunFileError, match="cannot have findings"):
        load_run(edited(project, flip))


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b'{"run_schema": 1, "run_schema": 2}', "duplicate key"),
        (b'{"a": NaN}', "non-finite"),
        (b"\xff", "not a valid run file"),
        (b"[", "not a valid run file"),
        (b"[]", "must be a JSON object"),
        (b"[" * (MAX_RUN_DEPTH + 2) + b"]" * (MAX_RUN_DEPTH + 2), "nesting deeper"),
        (b'{"a": "\\ud800"}', "surrogate"),
    ],
)
def test_a_hostile_run_file_is_refused(tmp_path: Path, raw: bytes, message: str) -> None:
    path = tmp_path / "x.run.json"
    path.write_bytes(raw)
    with pytest.raises(RunFileError, match=message):
        load_run(path)


def test_an_oversized_run_is_refused_before_it_is_read(tmp_path: Path) -> None:
    path = tmp_path / "big.run.json"
    with path.open("wb") as f:
        f.truncate(MAX_RUN_BYTES + 1)
    with pytest.raises(RunFileError, match="limit"):
        load_run(path)


def test_a_missing_run_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RunFileError, match="cannot read"):
        load_run(tmp_path / "missing.run.json")


def test_a_saved_run_cannot_be_dispatched(project: Path) -> None:
    saved(project, "suite.toml", "--plans-dir", "candidates/v3")
    calls: list[str] = []
    runner = ReferenceRunner(
        project / "contract.toml",
        project / "catalog.toml",
        {"build": lambda call: calls.append("build")},
    )
    loaded = load_run(project / "out.run.json")
    for not_an_approval in (loaded, loaded.run, loaded.run["cases"][0]):
        with pytest.raises(DispatchBlocked):
            runner.dispatch(not_an_approval)  # type: ignore[arg-type]
    assert calls == []


def test_run_suite_is_usable_from_the_sdk(project: Path) -> None:
    run = run_suite(load_suite(project / "suite.toml"), project / "candidates/v3", {"model": "x"})
    assert run["status"] == "passed"
    assert run["labels"] == {"model": "x"}


# --- declared types are enforced before anything relies on them ---------------------
WRONG_VALUES: tuple[Any, ...] = ([], {}, 1, -1, True, None, "x", 1.5, ["a"], {"a": 1})


def _paths(value: Any, prefix: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    found = [prefix] if prefix else []
    if isinstance(value, dict):
        for key, item in value.items():
            found += _paths(item, (*prefix, key))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            found += _paths(item, (*prefix, i))
    return found


def _replaced(doc: Any, path: tuple[Any, ...], value: Any) -> Any:
    copy = json.loads(json.dumps(doc))
    target = copy
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return copy


def test_a_wrong_type_anywhere_is_a_run_file_error_or_harmless(project: Path) -> None:
    (project / "candidates/v1/broken.json").write_text("{")
    path = write_suite(project, case("bad", "hotfix.json"))
    text = path.read_text().replace("candidates/v3", "candidates/v1")
    path.write_text(text + case("broken", "broken.json") + case("old", "legacy.json"))
    saved(project, str(path), name="sample.run.json")
    doc = json.loads((project / "sample.run.json").read_text())
    good = project / "sample.run.json"
    other = project / "mutated.run.json"
    tried = refused = 0
    for location in _paths(doc):
        for value in WRONG_VALUES:
            other.write_text(json.dumps(_replaced(doc, location, value)))
            tried += 1
            try:
                loaded = load_run(other)
            except RunFileError:  # anything else fails the test, naming the path
                refused += 1
                continue
            render_diff(compare_runs(load_run(good), loaded))
            render_diff(compare_runs(loaded, load_run(good)))
    assert tried > 2000
    assert refused > tried / 2


def test_a_list_verdict_is_an_actionable_error(project: Path) -> None:
    with pytest.raises(RunFileError, match=r"cases\[0\]\.verdict is invalid"):
        load_run(edited(project, _set("cases", 0, "verdict", [])))


def test_the_command_treats_a_list_verdict_as_invalid_input(project: Path) -> None:
    bad = edited(project, _set("cases", 0, "verdict", []))
    result = run_cli("suite", "diff", str(project / "good.run.json"), str(bad))
    assert result.returncode == 2
    assert result.stdout == ""
    assert "cases[0].verdict is invalid" in result.stderr
    assert "internal error" not in result.stderr


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (_set("cases", 0, "exit_code", False), "exit_code"),
        (_set("cases", 2, "exit_code", 1.0), "exit_code"),
        (_set("exit_code", True), "'exit_code'"),
        (_set("summary", "cases", 5.0), "'summary'"),
        (_set("cases", 0, "error_kind", "invalid_plan"), "error_kind"),
        (_set("cases", 2, "plan_evaluated", False), "must be evaluated"),
        (_set("cases", 0, "plan", "steps", "many"), "plan.steps"),
        (_set("cases", 2, "findings", 0, "title", 1), "title"),
        (_set("cases", 2, "findings", 0, "rule_version", 99), "not the version"),
        (
            _set("cases", 2, "findings", 0, "location", "source_span", {"line": "a", "column": 1}),
            "line",
        ),
        (_set("cases", 0, "case_id", "standard\n"), "case_id is invalid"),
        (_set("engine", "name", "another-tool"), "engine.name"),
        (_set("engine", "name", []), "engine.name"),
        (_set("report_schema", []), "report_schema"),
        (_set("runtime", "python", 3), "runtime.python"),
        (_set("note", 1), "'note'"),
        (_set("suite_errors", [{"code": [], "message": "m", "path": "p"}]), "suite_errors"),
        (_set("suite_errors", [{"code": "other", "message": "m", "path": "p"}]), "suite_errors"),
    ],
)
def test_declared_types_and_relationships_are_enforced(
    project: Path, change: Any, message: str
) -> None:
    with pytest.raises(RunFileError, match=message):
        load_run(edited(project, change))
