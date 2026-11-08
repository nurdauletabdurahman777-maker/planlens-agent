"""The command-line contract: exit codes, clean streams, no tracebacks."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from planlens_agent import RULES, cli

from helpers import TEMPLATE, run_cli


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A fresh copy of the template in a directory whose path contains spaces."""
    target = tmp_path / "my release project"
    proc = run_cli("init", "release-basic", str(target))
    assert proc.returncode == 0, proc.stderr
    return target


def no_escapes(*texts: str) -> None:
    for t in texts:
        assert "\x1b" not in t and "Traceback" not in t


@pytest.mark.parametrize(
    "plan, code",
    [
        ("valid.json", 0),
        ("valid-alternate-order.json", 0),
        ("reversed-review.json", 1),
        ("artifact-mismatch.json", 1),
    ],
)
def test_check_exit_codes_from_a_path_with_spaces(project: Path, plan: str, code: int) -> None:
    proc = run_cli("check", f"plans/{plan}", cwd=project)
    assert proc.returncode == code
    assert proc.stderr == ""
    no_escapes(proc.stdout)


def test_json_stdout_is_exactly_one_report_and_matches_saved_report(project: Path) -> None:
    proc = run_cli(
        "check",
        "plans/reversed-review.json",
        "--format",
        "json",
        "--output",
        "saved report.json",
        cwd=project,
    )
    assert proc.returncode == 1 and proc.stderr == ""
    report = json.loads(proc.stdout)
    saved = json.loads((project / "saved report.json").read_text())
    assert report == saved
    assert proc.stdout.isascii()
    assert report["diagnostics"][0]["rule_id"] == "plan/prerequisite-order"


def test_missing_file_is_exit_2_with_a_report_not_a_traceback(project: Path) -> None:
    proc = run_cli("check", "plans/does-not-exist.json", "--format", "json", cwd=project)
    assert proc.returncode == 2
    report = json.loads(proc.stdout)
    assert report["verdict"] == "error" and report["error_kind"] == "invalid_plan"
    no_escapes(proc.stdout, proc.stderr)


def test_missing_policy_defaults_are_exit_2(tmp_path: Path) -> None:
    plan = tmp_path / "p.json"
    shutil.copy(TEMPLATE / "plans" / "valid.json", plan)
    proc = run_cli("check", str(plan), cwd=tmp_path)  # no contract.toml / catalog.toml here
    assert proc.returncode == 2
    assert "contract/unreadable" in proc.stdout and "catalog/unreadable" in proc.stdout
    no_escapes(proc.stdout, proc.stderr)


def test_usage_errors_are_exit_2_without_traceback() -> None:
    for args in ([], ["check"], ["check", "x.json", "--format", "yaml"], ["frobnicate"]):
        proc = run_cli(*args)
        assert proc.returncode == 2, args
        assert proc.stdout == ""
        no_escapes(proc.stderr)


def test_unwritable_output_is_exit_2(project: Path) -> None:
    proc = run_cli("check", "plans/valid.json", "--output", "no/such/dir/r.json", cwd=project)
    assert proc.returncode == 2 and "cannot write report" in proc.stderr


def test_internal_error_is_never_a_pass(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("simulated bug")

    monkeypatch.setattr(cli, "check", boom)
    code = cli.main(["check", "anything.json"])
    err = capsys.readouterr().err
    assert code == 2 and "internal error" in err and "Traceback" not in err


def test_validate_policy(project: Path, tmp_path: Path) -> None:
    ok = run_cli("validate-policy", cwd=project)
    assert ok.returncode == 0 and ok.stdout.startswith("OK")
    contracts = Path(__file__).parent / "acceptance" / "contracts"
    for name, kind in (
        ("cyclic.toml", "contract/cycle"),
        ("unsupported-rule.toml", "contract/unsupported-rule"),
    ):
        bad = run_cli("validate-policy", "--contract", str(contracts / name), cwd=project)
        assert bad.returncode == 2 and kind in bad.stdout
        js = run_cli(
            "validate-policy", "--contract", str(contracts / name), "--format", "json", cwd=project
        )
        assert json.loads(js.stdout)["enforceable"] is False


def test_regress_reports_expectations_separately_from_plan_approval(project: Path) -> None:
    proc = run_cli("regress", "fixtures.toml", cwd=project)
    assert proc.returncode == 0
    assert "4 of 4 expectations met" in proc.stdout
    assert proc.stdout.count("[plan not approved for execution]") == 2
    js = json.loads(run_cli("regress", "fixtures.toml", "--format", "json", cwd=project).stdout)
    rejected = [c for c in js["cases"] if c["observed_verdict"] == "reject"]
    assert all(c["expectation_met"] and not c["plan_passes_required_checks"] for c in rejected)


def test_regress_unmet_expectation_is_exit_1(project: Path) -> None:
    f = project / "fixtures.toml"
    f.write_text(
        f.read_text().replace(
            'expect_rules = ["plan/artifact-identity"]',
            'expect_rules = ["plan/prerequisite-order"]',
        )
    )
    proc = run_cli("regress", "fixtures.toml", cwd=project)
    assert proc.returncode == 1 and "UNMET" in proc.stdout


def test_regress_invalid_fixture_file_is_exit_2(project: Path) -> None:
    (project / "bad.toml").write_text('schema = "planlens-agent/fixtures/v1"\n')
    proc = run_cli("regress", "bad.toml", cwd=project)
    assert proc.returncode == 2 and proc.stdout == ""
    no_escapes(proc.stderr)


def test_init_refuses_to_overwrite(project: Path) -> None:
    before = sorted(p.name for p in project.iterdir())
    proc = run_cli("init", "release-basic", str(project))
    assert proc.returncode == 2 and "nothing was written" in proc.stderr
    assert sorted(p.name for p in project.iterdir()) == before


def test_init_copies_exactly_the_template(project: Path) -> None:
    def files(root: Path) -> dict[str, bytes]:
        return {
            str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        }

    assert files(project) == files(TEMPLATE)


def test_rules_and_explain_work_offline() -> None:
    listing = run_cli("rules")
    assert listing.returncode == 0
    assert all(rid in listing.stdout for rid in RULES)
    exp = run_cli("explain", "plan/artifact-identity")
    assert exp.returncode == 0 and "not evidence that bytes exist" in exp.stdout
    miss = run_cli("explain", "plan/artifact-identty")
    assert miss.returncode == 2 and "did you mean plan/artifact-identity" in miss.stderr


def test_version() -> None:
    proc = run_cli("--version")
    assert proc.returncode == 0 and proc.stdout.startswith("planlens-agent 0.1.0")


def test_documented_fix_a_copy_flow_keeps_fixtures_intact(project: Path) -> None:
    """The journey found that fixing plans/ in place breaks fixtures.toml, so the
    quickstart now says to fix a copy. Follow it literally."""
    nxt = run_cli("init", "release-basic", str(project.parent / "second")).stdout
    assert "cp plans/reversed-review.json my-plan.json" in nxt
    shutil.copy(project / "plans" / "reversed-review.json", project / "my-plan.json")
    assert run_cli("check", "my-plan.json", cwd=project).returncode == 1
    data = json.loads((project / "my-plan.json").read_text())
    data["steps"].insert(3, data["steps"].pop(4))  # move review above deploy_prod
    (project / "my-plan.json").write_text(json.dumps(data))
    assert run_cli("check", "my-plan.json", cwd=project).returncode == 0
    regress = run_cli("regress", "fixtures.toml", cwd=project)
    assert regress.returncode == 0 and "4 of 4 expectations met" in regress.stdout
    assert not any(line != line.rstrip() for line in regress.stdout.splitlines())
