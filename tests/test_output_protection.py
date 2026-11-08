"""No command writes a report over a file it reads. Everything here uses the real CLI."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from planlens_agent.cli import main

from helpers import TEMPLATE, run_cli

PLAN = "plans/valid.json"
REJECTED = "plans/reversed-review.json"
CANDIDATE = "candidates/v1/hotfix.json"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    target = tmp_path / "project"
    shutil.copytree(TEMPLATE, target)
    return target


def snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def refused(project: Path, *args: str, cwd: Path | None = None) -> str:
    """Run a colliding command and require that nothing at all changed."""
    before = snapshot(project)
    done = run_cli(*args, cwd=cwd or project)
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert "is the input" in done.stderr
    assert "Choose a different output file" in done.stderr
    assert "internal error" not in done.stderr
    assert done.stdout == ""
    assert snapshot(project) == before
    return done.stderr


def check_args(output: str, fmt: str = "text") -> tuple[str, ...]:
    return ("check", PLAN, "--format", fmt, "--output", output)


def inspect_args(output: str) -> tuple[str, ...]:
    return ("inspect", PLAN, "--output", output)


def suite_args(output: str) -> tuple[str, ...]:
    return ("suite", "run", "suite.toml", "--overwrite", "--output", output)


COMMANDS = {
    "check-text": check_args,
    "check-json": lambda o: check_args(o, "json"),
    "check-sarif": lambda o: check_args(o, "sarif"),
    "inspect": inspect_args,
    "suite-overwrite": suite_args,
}


# --- every input of each command ----------------------------------------------------
@pytest.mark.parametrize("fmt", ["text", "json", "sarif"])
@pytest.mark.parametrize(
    ("target", "kind"),
    [(PLAN, "plan"), ("contract.toml", "contract"), ("catalog.toml", "catalog")],
)
def test_check_never_replaces_its_inputs(project: Path, target: str, kind: str, fmt: str) -> None:
    assert f"the input {kind}" in refused(project, *check_args(target, fmt))


@pytest.mark.parametrize(
    ("target", "kind"),
    [(PLAN, "plan"), ("contract.toml", "contract"), ("catalog.toml", "catalog")],
)
def test_inspect_never_replaces_its_inputs(project: Path, target: str, kind: str) -> None:
    assert f"the input {kind}" in refused(project, *inspect_args(target))


@pytest.mark.parametrize("command", ["check", "inspect"])
@pytest.mark.parametrize("flag", ["--contract", "--catalog"])
def test_explicit_owner_policy_files_are_protected(project: Path, command: str, flag: str) -> None:
    (project / "owner").mkdir()
    contract = shutil.copy(project / "contract.toml", project / "owner/c.toml")
    catalog = shutil.copy(project / "catalog.toml", project / "owner/k.toml")
    target = {"--contract": "owner/c.toml", "--catalog": "owner/k.toml"}[flag]
    refused(project, command, PLAN, "--contract", str(contract), "--catalog", str(catalog),
            "--output", target)  # fmt: skip


@pytest.mark.parametrize("overwrite", [True, False], ids=["overwrite", "no-overwrite"])
@pytest.mark.parametrize(
    ("target", "kind"),
    [
        ("suite.toml", "suite manifest"),
        ("contract.toml", "contract"),
        ("catalog.toml", "catalog"),
        (CANDIDATE, "candidate plan"),
    ],
)
def test_suite_run_never_replaces_its_inputs(
    project: Path, target: str, kind: str, overwrite: bool
) -> None:
    flags = ("--overwrite",) if overwrite else ()
    message = refused(project, "suite", "run", "suite.toml", *flags, "--output", target)
    assert f"the input {kind}" in message
    assert "--overwrite if" not in message  # never suggests overwriting an input


def test_a_plans_dir_override_protects_the_candidates_in_use(project: Path) -> None:
    refused(project, "suite", "run", "suite.toml", "--plans-dir", "candidates/v2", "--overwrite",
            "--output", "candidates/v2/hotfix.json")  # fmt: skip


def test_a_candidate_outside_the_override_is_not_an_input(project: Path) -> None:
    done = run_cli("suite", "run", "suite.toml", "--plans-dir", "candidates/v2", "--overwrite",
                   "--output", CANDIDATE, cwd=project)  # fmt: skip
    assert done.returncode == 1
    assert json.loads((project / CANDIDATE).read_text())["run_schema"] == "planlens-agent/run/v1"


def test_suite_inputs_resolve_from_the_manifest_not_the_working_directory(project: Path) -> None:
    refused(project, "suite", "run", "project/suite.toml", "--overwrite", "--output",
            "project/contract.toml", cwd=project.parent)  # fmt: skip


@pytest.mark.parametrize(
    "extra", ['plan = "standard-1.json"\n', 'plan = "hotfix.json"\nwaive = 1\n']
)
def test_candidates_of_entries_the_run_skips_are_still_protected(project: Path, extra: str) -> None:
    suite = project / "suite.toml"
    suite.write_text(suite.read_text() + f'\n[[cases]]\nid = "standard"\nsample = "1"\n{extra}')
    target = "candidates/v1/standard-1.json" if "standard" in extra else CANDIDATE
    refused(project, *suite_args(target))


def test_an_unusable_manifest_cannot_be_replaced(project: Path) -> None:
    (project / "suite.toml").write_text("not = [toml")
    before = snapshot(project)
    done = run_cli(*suite_args("suite.toml"), cwd=project)
    assert done.returncode == 2
    assert "suite file is invalid" in done.stderr
    assert snapshot(project) == before


@pytest.mark.parametrize("command", ["check", "inspect"])
def test_a_missing_input_is_not_created_as_a_report(project: Path, command: str) -> None:
    refused(project, command, "absent.json", "--output", "absent.json")


# --- equivalent paths and aliases, for every command --------------------------------
@pytest.mark.parametrize("command", COMMANDS)
def test_equivalent_spellings_are_the_same_file(project: Path, command: str) -> None:
    build = COMMANDS[command]
    refused(project, *build("./contract.toml"))
    refused(project, *build("plans/../contract.toml"))
    refused(project, *build(str(project / "contract.toml")))


def _link(kind: str, source: Path, link: Path) -> None:
    try:
        getattr(os, kind)(source, link)
    except (OSError, NotImplementedError) as exc:  # e.g. Windows without the privilege
        pytest.skip(f"{kind} is not available here: {exc}")


@pytest.mark.parametrize("command", COMMANDS)
def test_a_symlink_to_an_input_is_that_input(project: Path, command: str, tmp_path: Path) -> None:
    link = tmp_path / "link.toml"
    _link("symlink", project / "contract.toml", link)
    refused(project, *COMMANDS[command](str(link)))


@pytest.mark.parametrize("command", COMMANDS)
def test_a_linked_directory_reaches_the_same_input(
    project: Path, command: str, tmp_path: Path
) -> None:
    alias = tmp_path / "alias"
    _link("symlink", project, alias)
    refused(project, *COMMANDS[command](str(alias / "contract.toml")))


@pytest.mark.parametrize("command", COMMANDS)
def test_a_hard_link_to_an_input_is_that_input(project: Path, command: str, tmp_path: Path) -> None:
    link = tmp_path / "hard.toml"
    _link("link", project / "contract.toml", link)
    refused(project, *COMMANDS[command](str(link)))


def test_a_symlinked_plan_input_is_protected_through_its_target(
    project: Path, tmp_path: Path
) -> None:
    link = tmp_path / "plan-link.json"
    _link("symlink", project / PLAN, link)
    refused(project, "check", str(link), "--output", PLAN)


@pytest.mark.parametrize("command", COMMANDS)
def test_a_case_variant_is_the_same_file_where_case_is_ignored(project: Path, command: str) -> None:
    if not (project / "CONTRACT.TOML").exists():
        pytest.skip("this filesystem is case-sensitive")
    refused(project, *COMMANDS[command]("CONTRACT.TOML"))


# --- ordinary use is unchanged ------------------------------------------------------
@pytest.mark.parametrize(("plan", "code"), [(PLAN, 0), (REJECTED, 1)], ids=["pass", "reject"])
def test_check_output_and_status_are_unchanged(project: Path, plan: str, code: int) -> None:
    before = snapshot(project)
    done = run_cli("check", plan, "--format", "json", "--output", "report.json", cwd=project)
    assert done.returncode == code
    assert (project / "report.json").read_text() == done.stdout
    after = snapshot(project)
    del after["report.json"]
    assert after == before


@pytest.mark.parametrize(("plan", "code"), [(PLAN, 0), (REJECTED, 1)], ids=["pass", "reject"])
def test_sarif_output_and_status_are_unchanged(project: Path, plan: str, code: int) -> None:
    done = run_cli("check", plan, "--format", "sarif", "--output", "r.sarif", cwd=project)
    assert done.returncode == code
    assert (project / "r.sarif").read_text() == done.stdout


@pytest.mark.parametrize(("plan", "code"), [(PLAN, 0), (REJECTED, 1)], ids=["pass", "reject"])
def test_inspect_output_and_status_are_unchanged(project: Path, plan: str, code: int) -> None:
    done = run_cli("inspect", plan, "--output", "r.html", cwd=project)
    assert done.returncode == code
    assert "<html" in (project / "r.html").read_text().lower()


def test_check_may_still_replace_an_earlier_report(project: Path) -> None:
    (project / "report.json").write_text("old")
    done = run_cli(*check_args("report.json"), cwd=project)
    assert done.returncode == 0
    assert json.loads((project / "report.json").read_text())["verdict"] == "pass"


def test_a_policy_file_not_in_use_is_not_an_input(project: Path) -> None:
    (project / "owner").mkdir()
    contract = shutil.copy(project / "contract.toml", project / "owner/c.toml")
    done = run_cli("check", PLAN, "--contract", str(contract), "--output", "contract.toml",
                   cwd=project)  # fmt: skip
    assert done.returncode == 0
    assert json.loads((project / "contract.toml").read_text())["verdict"] == "pass"


def test_overwrite_still_replaces_an_earlier_run(project: Path) -> None:
    (project / "earlier.run.json").write_text("old")
    done = run_cli(*suite_args("earlier.run.json"), cwd=project)
    assert done.returncode == 1
    assert json.loads((project / "earlier.run.json").read_text())["status"] == "rejected"


def test_an_existing_run_still_needs_overwrite(project: Path) -> None:
    (project / "earlier.run.json").write_text("old")
    done = run_cli("suite", "run", "suite.toml", "--output", "earlier.run.json", cwd=project)
    assert done.returncode == 2
    assert "already exists" in done.stderr
    assert (project / "earlier.run.json").read_text() == "old"


def test_a_symlink_to_an_unrelated_file_is_an_ordinary_output(
    project: Path, tmp_path: Path
) -> None:
    real = tmp_path / "real.json"
    real.write_text("old")
    link = tmp_path / "ordinary-link.json"
    _link("symlink", real, link)
    done = run_cli(*check_args(str(link)), cwd=project)
    assert done.returncode == 0
    assert json.loads(real.read_text())["verdict"] == "pass"


# --- filesystem errors --------------------------------------------------------------
@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("output", ["no/such/dir/out", "x" * 300 + ".out"], ids=["missing", "long"])
def test_filesystem_errors_are_reported_normally(project: Path, command: str, output: str) -> None:
    before = snapshot(project)
    done = run_cli(*COMMANDS[command](output), cwd=project)
    assert done.returncode == 2
    assert "cannot write" in done.stderr
    assert "internal error" not in done.stderr
    assert snapshot(project) == before


def test_a_directory_as_output_is_reported_normally(project: Path) -> None:
    done = run_cli(*check_args("plans"), cwd=project)
    assert done.returncode == 2
    assert "cannot write" in done.stderr


def test_an_output_whose_identity_cannot_be_established_is_not_written(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = os.stat

    def stat(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if str(path) == "report.json":
            raise PermissionError(13, "Permission denied")
        return real(path, *args, **kwargs)

    monkeypatch.chdir(project)
    monkeypatch.setattr(os, "stat", stat)
    assert main(["check", PLAN, "--output", "report.json"]) == 2
    assert not (project / "report.json").exists()
