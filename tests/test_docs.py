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
    ROOT / "SECURITY.md",
    ROOT / "CODE_OF_CONDUCT.md",
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
CASE = README[README.index("## The example in detail") : README.index("## Scope and evidence")]
GALLERY = README[README.index("## See it work") : README.index("## How it fits")]
FITS = README[README.index("## How it fits") : README.index("## The example in detail")]
OPENING = README[: README.index("## Install and try it")]
CAPABILITIES = (ROOT / "docs" / "media" / "capabilities-transcript.txt").read_text(encoding="utf-8")
OWNER_REPO = "https://github.com/nurdauletabdurahman777-maker/planlens-agent"
LINKEDIN = "https://www.linkedin.com/in/nurdaulet-abdrakhman-523784375"


def flat(text: str) -> str:
    """Text with every run of whitespace as one space, so line wrapping does not matter."""
    return " ".join(text.split())


def test_images_have_descriptive_alt_text_and_reasonable_size() -> None:
    for start in (
        "Walkthrough of an authored example",
        "PlanLens inspector",
        "Terminal output of planlens-agent",
        "Authored Python from examples/guarded_runner.py",
        "Diagram. Your planner",
    ):
        assert len(re.search(f'alt="({re.escape(start)}[^"]+)"', README).group(1)) > 80, start  # type: ignore[union-attr]
    for tag in re.findall(r"<img [^>]*>", README):
        assert re.search(r'alt="[^"]{6,}"', tag), tag
    limits = {
        "demo.gif": 600_000,
        "demo.png": 400_000,
        "demo-narrow.gif": 600_000,
        "demo-narrow.png": 500_000,
        "inspector.png": 500_000,
        "inspector-light.png": 500_000,
        "inspector-finding.png": 300_000,
        "inspector-finding-light.png": 300_000,
        "inspector-finding-narrow.png": 300_000,
        "inspector-finding-narrow-light.png": 300_000,
        "social-preview.png": 1_000_000,
        "suite-diff.png": 300_000,
        "suite-diff-narrow.png": 300_000,
        "gate.png": 300_000,
        "gate-narrow.png": 300_000,
        "architecture-light.svg": 200_000,
        "architecture-dark.svg": 200_000,
    }
    for name, limit in limits.items():
        assert (ROOT / "docs" / "media" / name).stat().st_size < limit, name


def test_the_opening_is_the_hero_and_one_explanation() -> None:
    assert '<h1 align="center">Catch plan errors before execution.</h1>' in OPENING
    assert (
        "Check structured AI-agent plans against your workflow rules. "
        "See which requirement failed and the steps involved." in OPENING
    )
    assert OPENING.index("Catch plan errors") < OPENING.index("Check structured")
    assert OPENING.index("Check structured") < OPENING.index("docs/media/demo.gif")


def test_the_old_slogan_is_gone_and_the_hero_is_not_repeated() -> None:
    for text in (README, (ROOT / "README.pypi.md").read_text(encoding="utf-8")):
        assert "Lint AI-agent plans" not in text
        assert text.count("Catch plan errors before execution") == 1


def test_the_opening_states_its_audience_and_scope() -> None:
    for phrase in (
        "plan-and-execute agents",
        "structured, sequential plan",
        "required actions, step order, artifact references and same-artifact rules",
        "does not run your steps",
        "calls no model",
        "#install-and-try-it",
    ):
        assert phrase.lower() in flat(OPENING).lower(), phrase


BADGES = {  # badge image -> where it links
    f"{OWNER_REPO}/actions/workflows/ci.yml/badge.svg?branch=main": (
        f"{OWNER_REPO}/actions/workflows/ci.yml?query=branch%3Amain"
    ),
    "https://img.shields.io/pypi/v/planlens-agent?label=PyPI&color=20242C": (
        "https://pypi.org/project/planlens-agent/"
    ),
    "https://img.shields.io/badge/Python-3.11%2B-20242C": "pyproject.toml",
    "https://img.shields.io/badge/License-Apache--2.0-20242C": "LICENSE",
}
LINKED_IMAGE = re.compile(r'<a href="([^"]+)"><img alt="[^"]+" src="([^"]+)"></a>')


def test_the_badges_are_live_ci_and_true_facts_each_linked() -> None:
    linked = {src: href for href, src in LINKED_IMAGE.findall(OPENING)}
    assert linked == BADGES
    assert PROJECT["project"]["requires-python"] == ">=3.11"
    assert PROJECT["project"]["license"] == "Apache-2.0"
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "name: CI" in workflow.splitlines()


def test_no_popularity_coverage_or_adoption_badges() -> None:
    sources = " ".join(re.findall(r'src="([^"]+)"', README)).lower()
    for word in ("stars", "downloads", "dm/", "coverage", "codecov", "followers", "contributors"):
        assert word not in sources, word


def test_one_primary_visual_comes_before_the_details() -> None:
    images = re.findall(r"<img [^>]*>", OPENING)
    badges = [tag for tag in images if any(f'src="{src}"' in tag for src in BADGES)]
    assert len(badges) == len(BADGES)
    assert len(images) - len(badges) == 2  # the wordmark and the walkthrough
    assert README.index("docs/media/demo.gif") < README.index("## Install and try it")
    assert README.index("## Install and try it") < README.index("```json")
    assert README.index("## Install and try it") < README.index("inspector-finding")
    assert "inspector-finding" not in OPENING


def test_the_walkthrough_has_narrow_and_motion_free_versions() -> None:
    assert 'media="(max-width: 600px)" srcset="docs/media/demo-narrow.gif"' in OPENING
    assert 'media="(prefers-reduced-motion: reduce)" srcset="docs/media/demo.png"' in OPENING
    assert (
        'media="(prefers-reduced-motion: reduce) and (max-width: 600px)" '
        'srcset="docs/media/demo-narrow.png"' in OPENING
    )
    assert OPENING.index("prefers-reduced-motion") < OPENING.index(
        '(max-width: 600px)" srcset="docs/media/demo-narrow.gif'
    )
    assert '<a href="docs/media/demo.png">Static version</a>' in OPENING


def size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if data.startswith(b"GIF8"):
        return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
    assert data.startswith(b"\x89PNG")
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def test_each_static_summary_has_the_footprint_of_its_walkthrough() -> None:
    for stem in ("demo", "demo-narrow"):
        media = ROOT / "docs" / "media"
        assert size(media / f"{stem}.png") == size(media / f"{stem}.gif"), stem


def test_the_caption_keeps_the_limits_visible_and_the_edit_human() -> None:
    (caption,) = [x for x in OPENING.splitlines() if "Authored example" in x and "<sub>" in x]
    for phrase in (
        "Authored example",
        "references a plan declares",
        "nothing is built, tested or deployed",
        "a person makes the edit",
        "editorial",
    ):
        assert phrase in caption, phrase


def test_the_walkthrough_notes_do_not_claim_a_repair() -> None:
    source = (ROOT / "scripts" / "make_demo_media.py").read_text(encoding="utf-8")
    for phrase in (
        "A person changes one reference in a copy. PlanLens does not edit plans.",
        "Nothing was built, tested or deployed.",
        "a copy edited by hand",
        '"Plan references"',
        '"Diagnosis"',
        '"Manual edit"',
        '"Recheck"',
    ):
        assert phrase in source, phrase
    assert "assert 12 <= total <= 18" in source
    assert "Editorial note" not in source


def test_the_inspector_evidence_states_its_limits() -> None:
    for phrase in (
        "A real report from <code>planlens-agent inspect</code> for the authored example",
        "<code>test_rc</code> and <code>scan_rc</code> reference <code>build_rc.artifact</code>",
        "<code>deploy_prod</code> references <code>build_hotfix.artifact</code>",
        "did not inspect artifact bytes, run tests or deploy anything",
        "cannot tell which reference is the wrong one",
    ):
        assert phrase in flat(GALLERY), phrase


def test_the_inspector_evidence_selects_by_width_and_theme() -> None:
    for name in ("inspector-finding-narrow.png", "inspector-finding-narrow-light.png"):
        assert 'media="(max-width: 600px)' in GALLERY and f"docs/media/{name}" in GALLERY
    assert (
        'media="(prefers-color-scheme: dark)" srcset="docs/media/inspector-finding.png"' in GALLERY
    )


def test_each_capability_visual_has_a_narrow_version_and_no_motion() -> None:
    for stem in ("suite-diff", "gate"):
        assert f'media="(max-width: 600px)" srcset="docs/media/{stem}-narrow.png"' in GALLERY
        assert f'src="docs/media/{stem}.png"' in GALLERY
        assert size(ROOT / "docs" / "media" / f"{stem}.png")[0] == 1200
        assert size(ROOT / "docs" / "media" / f"{stem}-narrow.png")[0] == 720
    assert ".gif" not in GALLERY + FITS


def test_the_captions_say_what_is_real_authored_and_editorial() -> None:
    captions = [flat(x) for x in re.findall(r"<sub>(.*?)</sub>", GALLERY, re.S)]
    assert len(captions) == 3
    inspector, suite, gate = captions
    assert "A real report" in inspector and "authored example" in inspector
    for phrase in ("Real output", "authored", "marks omitted lines", "editorial"):
        assert phrase in suite, phrase
    for phrase in ("authored example whose handlers only print", "output is real", "editorial"):
        assert phrase in gate, phrase
    for caption in (suite, gate):
        assert '<a href="docs/media/capabilities-transcript.txt">Complete transcript</a>' in caption


def test_the_diagram_has_both_themes_and_matches_its_description() -> None:
    assert 'srcset="docs/media/architecture-dark.svg"' in FITS
    assert 'src="docs/media/architecture-light.svg"' in FITS
    for theme in ("light", "dark"):
        svg = (ROOT / "docs" / "media" / f"architecture-{theme}.svg").read_text(encoding="utf-8")
        assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 560 424"')
        assert 'role="img" aria-label="' in svg
        for token in ("<text", "href", "url(", "<image", "<script", "@import"):
            assert token not in svg, token
    source = (ROOT / "scripts" / "make_demo_media.py").read_text(encoding="utf-8")
    alt = re.search(r'alt="(Diagram\.[^"]+)"', FITS).group(1)  # type: ignore[union-attr]
    for drawn in (
        "Your planner",
        "Your rules",
        "Execution gate",
        "Diagnostics",
        "exit 0",
        "exit 2",
    ):
        assert f'"{drawn}' in source or drawn in source, drawn
        assert drawn.lower().replace("your ", "") in alt.lower(), drawn


# A saved run records the Python version, so the digest `suite diff` prints for each run file
# depends on the interpreter that wrote it. Only those two digests are set aside.
RUN_FILE_DIGEST = re.compile(r"^(  (?:baseline|candidate) +\S+ +)sha256:[0-9a-f]{12}", re.M)


def without_run_digests(text: str) -> str:
    return RUN_FILE_DIGEST.sub(r"\1sha256:<run file>", text)


def test_the_capability_transcript_is_real_output(tmp_path: Path) -> None:
    import shutil

    work = tmp_path / "demo"
    shutil.copytree(TEMPLATE, work)
    transcript = without_run_digests(CAPABILITIES)
    assert transcript.count("sha256:<run file>") == 2
    for plans, saved in (("candidates/v1", "v1.json"), ("candidates/v2", "v2.json")):
        done = run_cli(
            "suite", "run", "suite.toml", "--plans-dir", plans, "--output", saved, cwd=work
        )
        assert done.returncode == 1
        command = f"$ planlens-agent suite run suite.toml --plans-dir {plans} --output {saved}"
        assert f"{command}\n{done.stdout.rstrip()}\n[exit 1]" in CAPABILITIES
    done = run_cli("suite", "diff", "v1.json", "v2.json", cwd=work)
    assert done.returncode == 1
    shown = without_run_digests(done.stdout.rstrip())
    assert f"$ planlens-agent suite diff v1.json v2.json\n{shown}\n[exit 1]" in transcript
    shutil.copy(ROOT / "examples" / "guarded_runner.py", work)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    gate = subprocess.run(
        [sys.executable, "guarded_runner.py"], cwd=work, capture_output=True, text=True, env=env
    )
    assert gate.returncode == 0, gate.stderr
    assert f"$ python guarded_runner.py\n{gate.stdout.rstrip()}\n[exit 0]" in CAPABILITIES
    assert "/Users/" not in CAPABILITIES and "/private/" not in CAPABILITIES


def test_the_capability_visuals_quote_only_real_lines() -> None:
    source = (ROOT / "scripts" / "make_demo_media.py").read_text(encoding="utf-8")
    for phrase in (
        'assert diff[n] == "RESULT: NEW VIOLATIONS (exit 1)"',
        "assert source.count(code) == 1, code",
        'assert blocked.endswith("(dispatched: 0)"), blocked',
        "font-variant-ligatures:none",
    ):
        assert phrase in source, phrase


@pytest.mark.parametrize(
    "word",
    [
        "revolutionary",
        "next-generation",
        "seamless",
        "enterprise-grade",
        "production-ready",
        "stars",
        "blazing",
        "trusted by",
        "testimonial",
    ],
)
def test_no_promotional_or_unsupported_claims(word: str) -> None:
    assert word not in README.lower()


def test_the_page_does_not_lean_on_the_linter_label() -> None:
    assert README.lower().count("linter") <= 1


def test_security_and_conduct_use_only_the_approved_contact() -> None:
    for name in ("SECURITY.md", "CODE_OF_CONDUCT.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert LINKEDIN in text, name
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), name  # no email address
        assert "Nurdaulet Abdrakhman" in text, name
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "Do not open a public issue" in security and "0.1.x" in security
    assert "no fixed response time" in flat(security)


BRAND = ROOT / "docs" / "brand"
MARK_PATH = (
    "M10 8H37C48.6 8 55 14.3 55 25S48.6 42 37 42H22V56H10Z"
    "M22 19V31H36C40.8 31 43 29.1 43 25S40.8 19 36 19Z"
)
APPROVED_MARK = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"
     role="img" aria-label="PlanLens">
  <path fill="#20242C" fill-rule="evenodd"
    d="{MARK_PATH}"/>
  <rect x="43" y="47" width="12" height="12" rx="3" fill="#CB7100"/>
</svg>
"""
VARIANTS = {
    "light": ("#20242C", "#CB7100"),
    "dark": ("#EEF0F4", "#F5A623"),
    "mono-light": ("#20242C", "#20242C"),
    "mono-dark": ("#EEF0F4", "#EEF0F4"),
}


def test_the_mark_is_exactly_the_approved_planform() -> None:
    assert (BRAND / "planlens-mark-light.svg").read_text(encoding="utf-8") == APPROVED_MARK


@pytest.mark.parametrize("variant", VARIANTS)
def test_every_variant_uses_its_colours(variant: str) -> None:
    ink, accent = VARIANTS[variant]
    for kind in ("mark", "wordmark"):
        svg = (BRAND / f"planlens-{kind}-{variant}.svg").read_text(encoding="utf-8")
        assert f'<path fill="{ink}" fill-rule="evenodd"\n    d="{MARK_PATH}"/>' in svg
        assert f'<rect x="43" y="47" width="12" height="12" rx="3" fill="{accent}"/>' in svg
        assert set(re.findall(r'fill="([^"]+)"', svg)) == {ink, accent}


@pytest.mark.parametrize("variant", VARIANTS)
def test_the_lockup_places_the_existing_lettering_beside_the_mark(variant: str) -> None:
    svg = (BRAND / f"planlens-wordmark-{variant}.svg").read_text(encoding="utf-8")
    assert 'viewBox="0 0 286 64"' in svg
    assert svg.count('<g transform="translate(0,-1) scale(1.35)">') == 1
    assert svg.count("<path") == 2


@pytest.mark.parametrize("path", sorted(BRAND.glob("*.svg")), ids=lambda p: p.name)
def test_brand_files_are_transparent_outlined_and_self_contained(path: Path) -> None:
    svg = path.read_text(encoding="utf-8")
    assert 'role="img" aria-label="PlanLens"' in svg
    for token in ("<text", "href", "url(", "<image", "<style", "style=", "@import", "<script"):
        assert token not in svg, token
    assert svg.count("<rect") == 1 and '<rect x="43" y="47"' in svg  # no background shape


def test_the_case_is_collapsed_and_labelled() -> None:
    assert "<details>" in CASE and "<details open" not in CASE
    for summary in ("The plan's steps", "The diagnosis and a correction"):
        assert f"<summary><strong>{summary}</strong></summary>" in CASE
    assert CASE.count("<details>") == CASE.count("</details>") == 2
    assert CASE.count("Fragment of the authored plan") == 1
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


def test_pip_confinement_has_no_whitespace_for_pip_to_split() -> None:
    links = docs_check.offline_pip(Path("/a folder/with spaces/wheelhouse"))["PIP_FIND_LINKS"]
    assert links.startswith("file://") and not any(c.isspace() for c in links)


@windows
def test_pip_confinement_points_at_the_wheelhouse_when_the_path_has_spaces(tmp_path: Path) -> None:
    home = tmp_path / "a folder with spaces"
    home.mkdir()
    wheel, checkout = home / "x.whl", home / "checkout"
    wheel.write_bytes(b"")
    checkout.mkdir()
    probe = (
        'python3 -c "import os, pathlib, urllib.parse, urllib.request; '
        "url = os.environ['PIP_FIND_LINKS']; "
        "folder = pathlib.Path(urllib.request.url2pathname(urllib.parse.urlparse(url).path)); "
        "print('one-folder' if not any(c.isspace() for c in url) and folder.is_dir() "
        "and any(folder.glob('*.whl')) else 'split')\""
    )
    done = run(
        home,
        sh(probe, 'contains="one-folder"'),
        extra=("--wheel", str(wheel), "--checkout", str(checkout)),
    )
    assert done.returncode == 0, done.stdout


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
