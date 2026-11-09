"""The workflows keep the gate honest. Checked as text: no YAML parser is a dependency."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from helpers import ROOT

WORKFLOWS = ROOT / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
DEMO = WORKFLOWS / "consumer-demo.yml"
PUBLISH = WORKFLOWS / "publish.yml"


def job(text: str, name: str) -> str:
    """The lines of one job, from its header to the next job or the end."""
    match = re.search(rf"^  {name}:\n(.*?)(?=^  [a-z-]+:\n|\Z)", text, re.M | re.S)
    assert match, name
    return match.group(1)


@pytest.mark.parametrize("path", [CI, DEMO, PUBLISH], ids=lambda p: p.name)
def test_nothing_can_swallow_a_gate_or_reach_a_secret(path: Path) -> None:
    code = "\n".join(
        x for x in path.read_text(encoding="utf-8").splitlines() if not x.strip().startswith("#")
    )
    for token in (
        "continue-on-error",
        "pull_request_target",
        "|| true",
        "|| exit 0",
        "set +e",
        "secrets.",
        "API_KEY",
        "github.event.",
    ):
        assert token not in code, token


@pytest.mark.parametrize("path", [CI, DEMO, PUBLISH], ids=lambda p: p.name)
def test_every_action_is_pinned_to_a_full_commit(path: Path) -> None:
    uses = re.findall(r"uses:\s*(\S+)", path.read_text(encoding="utf-8"))
    assert uses
    for action in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", action), action


@pytest.mark.parametrize("path", [CI, DEMO, PUBLISH], ids=lambda p: p.name)
def test_default_permissions_are_read_only(path: Path) -> None:
    assert re.search(r"^permissions:\n  contents: read\n", path.read_text(encoding="utf-8"), re.M)


@pytest.mark.parametrize("path", [CI, DEMO, PUBLISH], ids=lambda p: p.name)
def test_checkouts_do_not_keep_credentials(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count("actions/checkout@") == text.count("persist-credentials: false")


def test_publishing_only_starts_by_hand() -> None:
    assert re.search(r"^on:\n  workflow_dispatch:\n    inputs:", PUBLISH.read_text("utf-8"), re.M)
    assert not re.search(
        r"^  (push|pull_request|schedule|release):", PUBLISH.read_text("utf-8"), re.M
    )


def test_only_the_publish_job_gets_an_identity_token_and_uses_the_environment() -> None:
    text = PUBLISH.read_text("utf-8")
    assert text.count("id-token: write") == 1 and "id-token: write" in job(text, "publish")
    assert "name: pypi\n" in job(text, "publish") and "environment" not in job(text, "check")


def test_the_published_pair_is_the_checked_pair_and_is_never_rebuilt() -> None:
    text = PUBLISH.read_text("utf-8")
    check, publish = job(text, "check"), job(text, "publish")
    assert check.count("uv build") == 1 and "scripts/release_check.py" in check
    assert (
        check.index("uv build") < check.index("release_check.py") < check.index("upload-artifact")
    )
    assert "uv build" not in publish and "actions/checkout" not in publish
    assert "needs: check" in publish
    assert "sha256sum --check --strict" in publish
    assert publish.index("sha256sum") < publish.index("gh-action-pypi-publish")


def test_publishing_stops_unless_tag_commit_and_main_agree() -> None:
    check = job(PUBLISH.read_text("utf-8"), "check")
    for condition in (
        'test "$REF" = "refs/tags/v$VERSION"',
        'test "$(git rev-parse HEAD)" = "$COMMIT"',
        'test "$(git rev-parse refs/remotes/origin/main)" = "$COMMIT"',
    ):
        assert condition in check, condition
    assert check.index("refs/remotes/origin/main") < check.index("uv build")


def test_only_the_sarif_job_can_write_security_events() -> None:
    text = DEMO.read_text(encoding="utf-8")
    assert text.count("security-events: write") == 1
    assert "security-events: write" in job(text, "sarif-demo")
    assert "write" not in CI.read_text(encoding="utf-8").replace("overwrite", "")


def test_the_demo_only_starts_by_hand() -> None:
    assert re.search(r"^on:\n  workflow_dispatch:\n\n", DEMO.read_text(encoding="utf-8"), re.M)


def test_the_rejecting_demo_runs_a_real_failing_gate_and_keeps_diagnostics() -> None:
    reject = job(DEMO.read_text(encoding="utf-8"), "reject-demo")
    gate = reject.split("Gate on rejecting candidates")[1].split("- name:")[0]
    assert "suite run suite.toml --plans-dir candidates/v2" in gate
    assert "if:" not in gate  # the step runs normally and its failure stands
    upload = reject.split("actions/upload-artifact@")[1]
    assert "if: always()" in upload


def test_uploads_survive_a_failure() -> None:
    for path in (CI, DEMO):
        for block in path.read_text(encoding="utf-8").split("uses: ")[1:]:
            if block.startswith(("actions/upload-artifact", "github/codeql-action")):
                assert "if: always()" in block.split("with:")[0]


def test_ci_covers_every_local_stage_and_a_real_matrix() -> None:
    text = CI.read_text(encoding="utf-8")
    for stage in ("lint", "test", "package", "journey", "recipe", "pilot", "docs"):
        assert f"scripts/ci_local.py {stage}" in text
    assert "[ubuntu-latest, macos-latest, windows-latest]" in text
    assert '["3.11", "3.12", "3.13", "3.14"]' in text


def test_no_workflow_writes_a_baseline_back() -> None:
    for path in (CI, DEMO):
        text = path.read_text(encoding="utf-8")
        for token in ("git push", "git commit", "--overwrite", "contents: write"):
            assert token not in text


def test_the_core_stays_free_of_runtime_dependencies() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["dependencies"] == []
    for source in (ROOT / "src").rglob("*.py"):
        assert not re.search(r"^\s*(from|import) lang", source.read_text(encoding="utf-8"), re.M), (
            source
        )


def test_the_recipe_pins_the_version_it_was_tested_with() -> None:
    recipe = ROOT / "examples" / "langgraph_gate"
    pin = re.search(
        r"^langgraph==(\S+)$", (recipe / "requirements.in").read_text(encoding="utf-8"), re.M
    )
    assert pin
    lock = (recipe / "requirements.lock").read_text(encoding="utf-8")
    assert f"langgraph=={pin.group(1)} " in lock
    assert "--hash=sha256:" in lock
    assert pin.group(1) in (recipe / "README.md").read_text(encoding="utf-8")
