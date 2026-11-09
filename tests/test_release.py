"""The release candidate agrees with itself, and its release documents stay as reviewed."""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
import tomllib

import pytest

from planlens_agent import __version__

from helpers import ROOT

spec = importlib.util.spec_from_file_location(
    "release_check", ROOT / "scripts" / "release_check.py"
)
assert spec and spec.loader
release_check = importlib.util.module_from_spec(spec)
sys.modules["release_check"] = release_check
spec.loader.exec_module(release_check)

PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
PROJECT = PYPROJECT["project"]
NOTES = (ROOT / "release" / f"NOTES_{__version__}.md").read_text(encoding="utf-8")
PUBLISHING = (ROOT / "release" / "PUBLISHING.md").read_text(encoding="utf-8")
PYPI = (ROOT / "README.pypi.md").read_text(encoding="utf-8")
TREE = {p.name for p in ROOT.iterdir() if p.is_file()} | {
    p.relative_to(ROOT).as_posix()
    for folder in (ROOT / "docs", ROOT / "release")
    for p in folder.rglob("*")
    if p.is_file()
}
BASE = release_check.GITHUB + "/blob/v9.9.9"
BODY_SHA256 = "a4cf1db33d2ca8071715911326450aab80167ede5fb082e06e1beb803f75ac10"


def test_every_declared_version_agrees() -> None:
    assert set(release_check.source_versions().values()) == {__version__}
    assert PROJECT["version"] == __version__


def test_the_current_demonstration_output_shows_this_version() -> None:
    transcript = (ROOT / "docs" / "media" / "demo-transcript.txt").read_text(encoding="utf-8")
    assert f"planlens-agent {__version__} - declared-plan check" in transcript
    assert "dev0" not in transcript


def test_the_recipe_installs_this_version() -> None:
    text = (ROOT / "examples" / "langgraph_gate" / "README.md").read_text(encoding="utf-8")
    assert f'"planlens-agent=={__version__}"' in text


def test_the_maturity_classifier_is_alpha_and_there_are_no_runtime_dependencies() -> None:
    assert release_check.ALPHA in PROJECT["classifiers"]
    assert not [c for c in PROJECT["classifiers"] if "Pre-Alpha" in c or "Stable" in c]
    assert PROJECT["dependencies"] == []


def test_the_pypi_description_is_explicitly_in_the_anchored_sdist() -> None:
    assert PROJECT["readme"] == "README.pypi.md"
    assert "/README.pypi.md" in PYPROJECT["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]


def test_the_pypi_description_has_only_tag_bound_links_to_real_files() -> None:
    assert release_check.description_problems(PYPI, __version__, TREE) == []
    assert f"/v{__version__}/docs/media/" in PYPI


@pytest.mark.parametrize(
    "text",
    [
        "![a](docs/media/x.png)",
        "[a](docs/quickstart.md)",
        '<img src="docs/media/x.png">',
        '<picture><source srcset="x"></picture>',
        "<details><summary>x</summary></details>",
        "[a](http://example.com)",
        f"[a]({release_check.GITHUB}/blob/main/README.md)",
        f"![a]({release_check.RAW}/main/README.md)",
        f"[a]({BASE}/does-not-exist.md)",
        f"![a]({release_check.RAW}/v9.9.9/does-not-exist.png)",
    ],
)
def test_content_that_would_break_on_pypi_is_refused(text: str) -> None:
    assert release_check.description_problems(text, "9.9.9", {"README.md"})


def test_good_links_and_code_blocks_are_accepted() -> None:
    text = f"[a]({BASE}/README.md#x) [b](#here)\n\n```sh\necho '[c](relative.md)'\n```\n"
    assert release_check.description_problems(text, "9.9.9", {"README.md"}) == []


def test_a_tag_that_is_not_the_version_is_refused() -> None:
    assert release_check.tag_problem("v0.1.1", "0.1.0")
    assert release_check.tag_problem("0.1.0", "0.1.0")
    assert release_check.tag_problem("v0.1.0", "0.1.0") is None
    assert release_check.tag_problem(None, "0.1.0") is None


def test_files_outside_the_anchored_selection_are_refused() -> None:
    allowed = {"src", "PKG-INFO"}
    assert release_check.sdist_problems({"r/src/a.py", "r/PKG-INFO"}, allowed, "r") == []
    assert release_check.sdist_problems({"r/review/notes.md"}, allowed, "r")
    assert release_check.sdist_problems({"elsewhere/src/a.py"}, allowed, "r")


def test_the_release_body_is_exactly_as_reviewed() -> None:
    body = NOTES[: NOTES.index("\n\n### Documentation")]
    assert hashlib.sha256(body.encode()).hexdigest() == BODY_SHA256


def test_the_release_notes_link_the_tagged_documents() -> None:
    links = re.findall(r"\]\(([^)]+)\)", NOTES)
    assert len(links) == 3
    assert release_check.description_problems(NOTES, __version__, TREE) == []
    for name in ("quickstart", "inspector", "evidence"):
        assert any(link.endswith(f"docs/{name}.md") for link in links), name


@pytest.mark.parametrize(
    "phrase",
    [
        "explicit owner authorization",
        "may cover a concrete, reviewed launch",
        "scripts/ci_gate.py",
        "Stop",
        "Trusted Publishing",
        "SHA256SUMS",
        "Do not rebuild",
        "without deleting or rewriting",
        "publish.yml",
    ],
)
def test_the_publishing_procedure_keeps_its_gates(phrase: str) -> None:
    assert phrase in PUBLISHING


def test_an_authorization_never_overrides_the_technical_gates() -> None:
    assert "The hold is absolute until the owner gives explicit authorization." in PUBLISHING
    assert "No authorization overrides the stop conditions" in PUBLISHING
    assert "Approval for one step does not extend" not in PUBLISHING


def test_the_positioning_is_consistent_in_metadata_and_the_release_title() -> None:
    assert PROJECT["description"].startswith("Catch plan errors before execution")
    assert "Lint AI-agent" not in PROJECT["description"] + PUBLISHING
    assert PUBLISHING.count("PlanLens v0.1.0 — Catch plan errors before execution") == 2
    assert PROJECT["name"] == "planlens-agent" and PROJECT["license"] == "Apache-2.0"


def test_provenance_separates_the_original_snapshot_from_the_current_state() -> None:
    text = (ROOT / "PROVENANCE.md").read_text(encoding="utf-8")
    assert text.index("**original snapshot**") < text.index("Created locally on 2026-11-06")
    assert "has gained a commit history and a GitHub remote" in text


@pytest.mark.skipif(not (ROOT / "pilot").exists(), reason="pilot/ is not in the sdist")
def test_pilot_documentation_is_marked_historical_and_points_to_current_files() -> None:
    text = (ROOT / "pilot" / "README.md").read_text(encoding="utf-8")
    assert "**Historical.**" in text and "still pending" not in text
    assert "[AUTHORS](../AUTHORS.md)" in text and "[NOTICE](../NOTICE)" in text


def test_the_launch_steps_are_in_the_agreed_order() -> None:
    steps = [
        "Review the final candidate",
        "Make the repository public",
        "Push the candidate",
        "Merge into main",
        "Verify the exact main commit",
        "Create the tag",
        "Publish to PyPI",
        "Verify installation from PyPI",
        "Publish the GitHub Release",
        "Prepare the announcement",
    ]
    positions = [PUBLISHING.index(f"{n}. {step}") for n, step in enumerate(steps, 1)]
    assert positions == sorted(positions)
