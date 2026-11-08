"""Rule documentation and rule usage stay consistent with the registry."""

from __future__ import annotations

import re
import runpy
from pathlib import Path

from planlens_agent import RULES

ROOT = Path(__file__).resolve().parents[1]


def test_rules_md_is_generated_from_the_registry() -> None:
    render = runpy.run_path(str(ROOT / "scripts" / "gen_rule_docs.py"))["render"]
    assert (ROOT / "docs" / "rules.md").read_text() == render(), (
        "docs/rules.md is stale; run python scripts/gen_rule_docs.py"
    )


def test_every_literal_rule_id_in_the_source_is_registered() -> None:
    src = "\n".join(p.read_text() for p in (ROOT / "src" / "planlens_agent").glob("*.py"))
    literal = set(re.findall(r'Diagnostic\(\s*"([a-z]+/[a-z-]+)"', src))
    assert literal, "pattern found nothing; the test is not checking anything"
    assert literal <= set(RULES), literal - set(RULES)


def test_every_rule_has_an_explanation() -> None:
    for info in RULES.values():
        assert info.title and len(info.summary) > 40, info.id


def test_acceptance_doc_is_generated_from_the_cases() -> None:
    render = runpy.run_path(str(ROOT / "scripts" / "gen_acceptance_doc.py"))["render"]
    assert (ROOT / "docs" / "acceptance-examples.md").read_text() == render(), (
        "docs/acceptance-examples.md is stale; run python scripts/gen_acceptance_doc.py"
    )
