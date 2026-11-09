"""The documentation points at real things, and its copy-and-run blocks stay runnable."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from planlens_agent import __version__, check

from helpers import ROOT, TEMPLATE, run_cli

DOCS = [
    ROOT / "README.md",
    ROOT / "README.pypi.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "examples" / "README.md",
    *sorted((ROOT / "docs").glob("*.md")),
    ROOT / "docs" / "brand" / "README.md",
]
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)\)")
IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
HTML_TARGET = re.compile(r'(?:src|srcset|href)="([^"]+)"')

spec = importlib.util.spec_from_file_location("docs_check", ROOT / "scripts" / "docs_check.py")
assert spec is not None and spec.loader is not None
docs_check = importlib.util.module_from_spec(spec)
sys.modules["docs_check"] = docs_check
spec.loader.exec_module(docs_check)


def slugs(text: str) -> set[str]:
    found = set()
    for title in re.findall(r"^#+ (.+)$", re.sub(r"```.*?```", "", text, flags=re.S), re.M):
        plain = re.sub(r"[`*]", "", title).strip().lower()
        found.add(re.sub(r"[^\w\- ]", "", plain).replace(" ", "-"))
    return found


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_relative_links_and_anchors_exist(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    for target in (
        LINK.findall(text) + [m[1] for m in IMAGE.findall(text)] + HTML_TARGET.findall(text)
    ):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, anchor = target.partition("#")
        resolved = (doc.parent / path).resolve() if path else doc
        assert resolved.exists(), f"{doc.name}: {target}"
        if anchor and resolved.suffix == ".md":
            assert anchor in slugs(resolved.read_text(encoding="utf-8")), f"{doc.name}: {target}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_copy_and_run_blocks_are_annotated_and_pasteable(doc: Path) -> None:
    assert docs_check.problems(docs_check.parse(doc.read_text(encoding="utf-8"))) == []


def test_the_journey_documents_have_runnable_blocks() -> None:
    for doc in (ROOT / "README.md", ROOT / "docs" / "quickstart.md"):
        assert len(docs_check.parse(doc.read_text(encoding="utf-8"))) >= 3


def test_an_unannotated_block_is_refused() -> None:
    assert docs_check.problems(docs_check.parse("```sh\necho hi\n```\n"))


def test_an_inline_comment_is_refused_because_zsh_would_run_it() -> None:
    text = "<!-- run -->\n```sh\necho hi # exit 0\n```\n"
    assert docs_check.problems(docs_check.parse(text))


README = (ROOT / "README.md").read_text(encoding="utf-8")
PLAN = (TEMPLATE / "plans" / "artifact-mismatch.json").read_text(encoding="utf-8")
TRANSCRIPT = (ROOT / "docs" / "media" / "demo-transcript.txt").read_text(encoding="utf-8")
CASE = README[README.index("## All required steps") : README.index("## Install and reproduce it")]


def test_images_have_descriptive_alt_text_and_reasonable_size() -> None:
    markdown = IMAGE.findall(README)
    assert [Path(path).name for _, path in markdown] == ["demo.gif"]
    assert len(markdown[0][0]) > 80
    assert len(re.search(r'alt="(PlanLens inspector[^"]+)"', README).group(1)) > 80  # type: ignore[union-attr]
    limits = {
        "demo.gif": 300_000,
        "demo.png": 200_000,
        "inspector.png": 500_000,
        "inspector-light.png": 500_000,
        "inspector-finding.png": 300_000,
        "inspector-finding-light.png": 300_000,
        "inspector-finding-narrow.png": 300_000,
        "inspector-finding-narrow-light.png": 300_000,
        "social-preview.png": 1_000_000,
    }
    for name, limit in limits.items():
        assert (ROOT / "docs" / "media" / name).stat().st_size < limit, name


def test_the_opening_answers_the_first_questions() -> None:
    opening = README[: README.index("## All required steps")]
    assert "Lint AI-agent plans before they run." in opening
    for phrase in (
        "plan-and-execute agents",
        "wrong order",
        "actions and workflow requirements",
        "sequential plan",
        "deterministic verdict",
        "#install-and-reproduce-it",
        "authored plan",
        "first public release",
    ):
        assert phrase.lower() in opening.lower(), phrase


def test_the_primary_preview_states_the_mismatch_in_text_and_its_limits() -> None:
    opening = README[: README.index("## All required steps")]
    assert "<picture>" in opening and "demo.gif" not in opening
    for phrase in (
        "`test_rc` and `scan_rc` reference `build_rc.artifact`",
        "`deploy_prod`\nreferences `build_hotfix.artifact`",
        "did not inspect artifact bytes, run tests or deploy anything",
        "cannot tell which\nreference is the wrong one",
    ):
        assert phrase in opening, phrase


def test_the_preview_selects_by_width_and_theme() -> None:
    for name in ("inspector-finding-narrow.png", "inspector-finding-narrow-light.png"):
        assert 'media="(max-width: 600px)' in README and f"docs/media/{name}" in README


@pytest.mark.parametrize(
    "word",
    [
        "revolutionary",
        "next-generation",
        "seamless",
        "enterprise-grade",
        "production-ready",
        "stars",
    ],
)
def test_no_promotional_or_unsupported_claims(word: str) -> None:
    assert word not in README.lower()


def test_the_brand_has_both_themes() -> None:
    for theme in ("light", "dark"):
        svg = (ROOT / "docs" / "brand" / f"planlens-wordmark-{theme}.svg").read_text()
        assert 'aria-label="PlanLens"' in svg and "<text" not in svg


def test_the_case_is_collapsed_and_labelled() -> None:
    assert "<details>" in CASE and "<details open" not in CASE
    assert "<summary><strong>Reveal the PlanLens diagnosis</strong></summary>" in CASE
    assert "Fragment of the authored plan" in CASE
    assert "saved result" in CASE


def test_the_case_fragment_is_the_real_plan() -> None:
    fence = re.search(r"```json\n(.*?)```", CASE, re.S)
    assert fence is not None
    lines = fence.group(1).splitlines()
    assert len(lines) == 6
    for line in lines:
        assert line in PLAN.splitlines(), line


def test_the_case_diagnosis_is_real_output() -> None:
    done = run_cli("check", "plans/artifact-mismatch.json", cwd=TEMPLATE)
    assert done.returncode == 1
    # The transcript was captured on POSIX; Windows prints this input path with a backslash.
    output = done.stdout.replace("plans\\artifact-mismatch.json", "plans/artifact-mismatch.json")
    assert output.rstrip("\n") in TRANSCRIPT
    for line in ("RESULT: REJECTED (exit 1) - 1 violation", "    why:      The artifact that is"):
        assert line in done.stdout and line in CASE
    assert "plan/artifact-identity" in done.stdout and "deploy-what-was-verified" in done.stdout


def test_the_case_references_are_the_real_findings() -> None:
    result = check(
        TEMPLATE / "plans/artifact-mismatch.json",
        TEMPLATE / "contract.toml",
        TEMPLATE / "catalog.toml",
    )
    (finding,) = result.diagnostics
    assert finding.rule_id == "plan/artifact-identity"
    for row in finding.observed:
        assert f"| `{row['step_id']}` | `{row['refers_to']}` |" in CASE


def test_the_case_correction_and_recheck_are_real() -> None:
    (old,) = [line for line in PLAN.splitlines() if "build_hotfix.artifact" in line]
    assert f"-{old}" in CASE
    assert "+" + old.replace("build_hotfix.artifact", "build_rc.artifact") in CASE
    fixed = PLAN.replace("build_hotfix.artifact", "build_rc.artifact")
    passed = check(json.loads(fixed), TEMPLATE / "contract.toml", TEMPLATE / "catalog.toml")
    assert passed.verdict == "pass"
    (line,) = [x for x in CASE.splitlines() if x.startswith("RESULT: PASS")]
    assert line in TRANSCRIPT and line in passed.render_text()


def test_the_transcript_has_no_local_paths() -> None:
    assert (
        "/Users/" not in TRANSCRIPT and "/private/" not in TRANSCRIPT and "/tmp/" not in TRANSCRIPT
    )


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_no_local_paths(doc: Path) -> None:
    assert "/Users/" not in doc.read_text(encoding="utf-8")


# --- the runner cannot be fooled by a later success -----------------------------------
windows = pytest.mark.skipif(os.name == "nt", reason="the documentation runner needs a POSIX shell")
FAIL = 'python3 -c "import sys; sys.exit(3)"'
OK = 'python3 -c "print(1)"'


def run(
    tmp_path: Path, text: str, name: str = "guide", extra: tuple[str, ...] = ()
) -> subprocess.CompletedProcess[str]:
    doc = tmp_path / f"{name}.md"
    doc.write_text(text)
    work = tmp_path / f"work-{name}"
    argv = [
        sys.executable,
        str(ROOT / "scripts" / "docs_check.py"),
        str(doc),
        "--python",
        sys.executable,
        "--work",
        str(work),
        *extra,
    ]
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def sh(body: str, attrs: str = "") -> str:
    return f"<!-- run {attrs} -->\n```sh\n{body}\n```\n\n"


@windows
def test_a_missing_script_fails_even_when_a_later_command_succeeds(tmp_path: Path) -> None:
    done = run(tmp_path, sh(f"python3 definitely-missing-sdk.py\n{OK}"))
    assert done.returncode == 1, done.stdout
    assert "command 1 of 2 exited 2, wanted 0: python3 definitely-missing-sdk.py" in done.stdout


@windows
def test_a_failed_setup_command_stops_the_block_and_cannot_be_hidden(tmp_path: Path) -> None:
    done = run(tmp_path, sh("cd no-such-directory\ntouch must-not-exist"))
    assert done.returncode == 1, done.stdout
    assert "command 1 of 2" in done.stdout
    assert not list((tmp_path / "work-guide").rglob("must-not-exist"))


@windows
def test_an_intended_final_exit_is_accepted(tmp_path: Path) -> None:
    done = run(tmp_path, sh(f"{OK}\n{FAIL}", "exit=3"))
    assert done.returncode == 0, done.stdout


@windows
def test_a_nonzero_exit_is_accepted_only_from_the_last_command(tmp_path: Path) -> None:
    done = run(tmp_path, sh(f"{FAIL}\n{OK}", "exit=3"))
    assert done.returncode == 1, done.stdout
    assert "command 1 of 2 exited 3, wanted 0" in done.stdout


@windows
def test_an_expected_failure_that_succeeds_is_refused(tmp_path: Path) -> None:
    assert run(tmp_path, sh(OK, "exit=1")).returncode == 1


@windows
def test_working_directory_activation_and_heredocs_carry_across_commands(tmp_path: Path) -> None:
    text = (
        sh("python3 -m venv env\n. env/bin/activate\nmkdir inner\ncd inner")
        + sh(
            "cat > note.txt <<'EOF'\n"
            "a; b | c $(d) # not shell\n"
            "EOF\n"
            "cat note.txt\n"
            "python -c \\\n"
            '  "import sys; print(sys.prefix.endswith(chr(101) + chr(110) + chr(118)))"',
            'contains="a; b | c $(d) # not shell" contains="True"',
        )
        + sh('python -c "import os; print(os.path.basename(os.getcwd()))"', 'contains="inner"')
    )
    done = run(tmp_path, text)
    assert done.returncode == 0, done.stdout


@windows
def test_a_block_that_kills_the_shell_is_not_counted_as_finished(tmp_path: Path) -> None:
    done = run(tmp_path, sh(f"exit 0\n{OK}"))
    assert done.returncode == 1, done.stdout
    assert "did not finish" in done.stdout


@pytest.mark.parametrize(
    "line",
    ["false; true", "false | true", "false || true", "x=$(false)", "echo `false`", "false &"],
)
def test_constructs_that_can_hide_a_failure_are_refused(line: str) -> None:
    found = docs_check.problems(docs_check.parse(f"<!-- run -->\n```sh\n{line}\n```\n"))
    assert any("can hide a failure" in item for item in found), found


@pytest.mark.parametrize(
    "line",
    ["sed 's/a;b|c/d/' in > out", 'echo "a; b | c"', "first && second", "tool > out 2>&1"],
)
def test_safe_lines_are_not_refused(line: str) -> None:
    assert docs_check.problems(docs_check.parse(f"<!-- run -->\n```sh\n{line}\n```\n")) == []


def test_heredoc_bodies_are_not_treated_as_shell() -> None:
    text = "<!-- run -->\n```sh\ncat > f <<'EOF'\na; b | c # d\nEOF\n```\n"
    assert docs_check.problems(docs_check.parse(text)) == []


@pytest.mark.parametrize("text", ["cat > f <<EOF\nx\nEOF", "cat > f <<'EOF'\nnever closed"])
def test_unsafe_heredocs_are_refused(text: str) -> None:
    assert docs_check.problems(docs_check.parse(f"<!-- run -->\n```sh\n{text}\n```\n"))


def test_commands_keep_continuations_and_heredocs_whole() -> None:
    body = "one \\\n  two\ncat > f <<'EOF'\nthree\nEOF\nfour\n"
    assert docs_check.commands(body) == ["one \\\n  two", "cat > f <<'EOF'\nthree\nEOF", "four"]


@pytest.mark.parametrize(
    "doc", [ROOT / "README.md", ROOT / "docs" / "quickstart.md", ROOT / "CONTRIBUTING.md"], ids=str
)
def test_every_command_fence_in_the_journey_documents_is_verified(doc: Path) -> None:
    fences = re.findall(r"^```(\w*)", doc.read_text(encoding="utf-8"), re.M)
    assert not {"console", "shell", "bash", "zsh"} & set(fences), fences


def test_the_development_setup_is_a_verified_block_that_selects_its_environment() -> None:
    blocks = docs_check.parse((ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8"))
    dev = [b for b in blocks if b.kind == "dev"]
    assert len(dev) == 1
    assert dev[0].body.startswith("uv run ")


# --- substitution stays active inside double quotes ------------------------------------
ACTIVE_IN_DOUBLE_QUOTES = [
    'echo "$(python3 definitely-missing-sdk.py)"',
    'echo "`python3 definitely-missing-sdk.py`"',
    'echo "before $(false) after"',
    'echo "before `false` after"',
    'echo "\\\\$(false)"',  # an escaped backslash, so the substitution is active
    'echo "\\\\`false`"',
    'echo "\\"$(false)"',  # an escaped quote does not close the string
    "echo \\\\$(false)",  # outside quotes too: an escaped backslash leaves it active
]
LITERAL_TEXT = [
    "echo '$(false)'",
    "echo '`false`'",
    "echo 'say \"$(false)\"'",
    'echo "\\$(false)"',
    'echo "\\`false\\`"',
    "echo \\$\\(false\\)",
    "echo \\;",
    "echo a\\|b",
    "echo \\`false\\`",
    'echo "cost: $HOME ${HOME} (x)"',
    'echo "it\'s"',
]


@pytest.mark.parametrize("line", ACTIVE_IN_DOUBLE_QUOTES)
def test_active_substitution_in_double_quotes_is_refused(line: str) -> None:
    found = docs_check.problems(docs_check.parse(f"<!-- run -->\n```sh\n{line}\n```\n"))
    assert any("can hide a failure" in item for item in found), found


@pytest.mark.parametrize("line", LITERAL_TEXT)
def test_literal_text_is_accepted(line: str) -> None:
    assert docs_check.problems(docs_check.parse(f"<!-- run -->\n```sh\n{line}\n```\n")) == []


def test_substitution_text_in_a_quoted_heredoc_body_is_literal() -> None:
    text = "<!-- run -->\n```sh\ncat > f <<'EOF'\n\"$(false)\" `false`\nEOF\n```\n"
    assert docs_check.problems(docs_check.parse(text)) == []


@pytest.mark.parametrize(
    "line",
    ['echo "$(python3 definitely-missing-sdk.py)"', 'echo "`python3 definitely-missing-sdk.py`"'],
    ids=["dollar-paren", "backtick"],
)
def test_the_runner_refuses_the_block_before_anything_runs(tmp_path: Path, line: str) -> None:
    done = run(tmp_path, sh(f"touch ran\n{line}"))
    assert done.returncode == 1, done.stdout
    assert "can hide a failure" in done.stdout
    assert not list(tmp_path.rglob("ran"))


@windows
def test_literal_text_really_is_literal_when_run(tmp_path: Path) -> None:
    body = 'echo \'$(false)\'\necho "\\`false\\`"\necho "\\$(hostname)"'
    done = run(tmp_path, sh(body, 'contains="$(false)" contains="`false`" contains="$(hostname)"'))
    assert done.returncode == 0, done.stdout


# --- owner-confirmed project details --------------------------------------------------
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
NOTICE = (ROOT / "NOTICE").read_text(encoding="utf-8")


@windows
def test_only_ordinary_blocks_are_confined_to_the_local_wheel(tmp_path: Path) -> None:
    wheel, checkout = tmp_path / "x.whl", tmp_path / "checkout"
    wheel.write_bytes(b"")
    checkout.mkdir()
    show = "python3 -c \"import os; print(os.environ.get('PIP_NO_INDEX', 'open'))\""
    text = sh(show, 'contains="1"') + sh(show, 'online contains="open"') + sh(show, 'contains="1"')
    done = run(tmp_path, text, extra=("--wheel", str(wheel), "--checkout", str(checkout)))
    assert done.returncode == 0, done.stdout
    assert "does not exercise PyPI" in done.stdout


def test_the_journey_installs_the_public_package_by_exact_version() -> None:
    for doc in (ROOT / "README.md", ROOT / "README.pypi.md", ROOT / "docs" / "quickstart.md"):
        text = doc.read_text(encoding="utf-8")
        assert f'python -m pip install "planlens-agent=={__version__}"' in text, doc.name
        assert "--branch" not in text and "wheelhouse" not in text, doc.name


def test_package_metadata_names_the_confirmed_people_in_order() -> None:
    project = PROJECT["project"]
    assert project["authors"] == [{"name": "Nurdaulet Abdrakhman"}, {"name": "Daniyal Kozyrev"}]
    assert project["maintainers"] == [{"name": "Nurdaulet Abdrakhman"}]
    assert project["dependencies"] == []
    assert (
        project["urls"]["Contact"] == "https://www.linkedin.com/in/nurdaulet-abdrakhman-523784375"
    )


def test_notice_keeps_product_research_and_oasis_attribution_separate() -> None:
    assert "Copyright 2026 Nurdaulet Abdrakhman\n" in NOTICE
    assert "Copyright 2026 Nurdaulet Abdrakhman and Daniyal Kozyrev, Apache-2.0" in NOTICE
    assert "OASIS work product" in NOTICE and "not under the Apache License" in NOTICE
    assert "pending" not in NOTICE


def test_every_root_document_the_readme_links_is_in_the_sdist() -> None:
    include = PROJECT["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    targets = LINK.findall(README)
    for target in [t for t in targets if not t.startswith(("http", "#", "docs/", "examples/"))]:
        assert f"/{target}" in include, target


def test_sdist_patterns_are_anchored_so_local_files_cannot_match_by_name() -> None:
    include = PROJECT["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    assert all(pattern.startswith("/") for pattern in include), include
