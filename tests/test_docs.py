"""The documentation points at real things, and its copy-and-run blocks stay runnable."""

from __future__ import annotations

import importlib.util
import re
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
