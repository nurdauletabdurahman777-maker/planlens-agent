"""The documentation points at real things, and its copy-and-run blocks stay runnable."""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import ROOT

DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)\)")
IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")

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
    for target in LINK.findall(text) + [m[1] for m in IMAGE.findall(text)]:
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
        assert len(docs_check.parse(doc.read_text(encoding="utf-8"))) >= 5


def test_an_unannotated_block_is_refused() -> None:
    assert docs_check.problems(docs_check.parse("```sh\necho hi\n```\n"))


def test_an_inline_comment_is_refused_because_zsh_would_run_it() -> None:
    text = "<!-- run -->\n```sh\necho hi # exit 0\n```\n"
    assert docs_check.problems(docs_check.parse(text))


def test_images_have_descriptive_alt_text_and_reasonable_size() -> None:
    images = IMAGE.findall((ROOT / "README.md").read_text(encoding="utf-8"))
    assert {Path(path).name for _, path in images} == {"demo.gif", "inspector.png"}
    for alt, _ in images:
        assert len(alt) > 80, alt
    for name, limit in (("demo.gif", 1_000_000), ("demo.png", 500_000), ("inspector.png", 500_000)):
        assert (ROOT / "docs" / "media" / name).stat().st_size < limit, name


def test_the_opening_screen_says_what_it_is_before_anything_else() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    opening = text[: text.index("## Install")]
    assert opening.startswith("# planlens-agent\n\n**Lint AI-agent plans before they run.**")
    for phrase in (
        "plan-and-execute agents",
        "action catalog",
        "workflow contract",
        "sequential plan",
        "deterministic verdict",
        "execution gate",
        "authored example",
        "private",
    ):
        assert phrase.lower() in opening.lower(), phrase


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_no_local_paths(doc: Path) -> None:
    assert "/Users/" not in doc.read_text(encoding="utf-8")


# --- the runner cannot be fooled by a later success -----------------------------------
windows = pytest.mark.skipif(os.name == "nt", reason="the documentation runner needs a POSIX shell")
FAIL = 'python3 -c "import sys; sys.exit(3)"'
OK = 'python3 -c "print(1)"'


def run(tmp_path: Path, text: str, name: str = "guide") -> subprocess.CompletedProcess[str]:
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


@pytest.mark.parametrize("doc", [ROOT / "README.md", ROOT / "docs" / "quickstart.md"], ids=str)
def test_every_command_fence_in_the_journey_documents_is_verified(doc: Path) -> None:
    fences = re.findall(r"^```(\w*)", doc.read_text(encoding="utf-8"), re.M)
    assert not {"console", "shell", "bash", "zsh"} & set(fences), fences


def test_the_development_setup_is_a_verified_block_that_selects_its_environment() -> None:
    blocks = docs_check.parse((ROOT / "README.md").read_text(encoding="utf-8"))
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
