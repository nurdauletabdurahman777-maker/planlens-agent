"""Write docs/rules.md from the rule registry used by `planlens-agent explain`.

Run:  python scripts/gen_rule_docs.py
A test fails if docs/rules.md is out of date.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from planlens_agent.diagnostics import CATEGORY_ORDER, RULES  # noqa: E402

EXIT = {
    "violation": 1,
    "invalid_plan": 2,
    "invalid_contract": 2,
    "invalid_catalog": 2,
    "unsupported": 2,
}


def render() -> str:
    lines = [
        "# Rules",
        "",
        "Generated from `src/planlens_agent/diagnostics.py` by `scripts/gen_rule_docs.py`.",
        "The same text is printed offline by `planlens-agent explain <rule>`.",
        "",
        "| category | meaning | exit code |",
        "| --- | --- | --- |",
        "| `violation` | the plan parsed but breaks the catalog or contract | 1 |",
        "| `invalid_plan`, `invalid_contract`, `invalid_catalog` | a document cannot be used | 2 |",
        "| `unsupported` | a requirement or semantics the engine cannot evaluate | 2 |",
        "",
    ]
    for cat in reversed(CATEGORY_ORDER):
        ids = sorted(r for r in RULES if RULES[r].category == cat)
        if not ids:
            continue
        lines += [f"## {cat}", ""]
        for rid in ids:
            info = RULES[rid]
            lines += [
                f"### `{rid}`",
                "",
                f"*{info.title}* - rule version {info.version}, exit code {EXIT[cat]}.",
                "",
                info.summary,
                "",
            ]
            if info.bad_example:
                lines += ["Triggers it:", "", "```", info.bad_example, "```", ""]
            if info.good_example:
                lines += ["Satisfies it:", "", "```", info.good_example, "```", ""]
            if info.limitations:
                lines += [f"Limits: {info.limitations}", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    (ROOT / "docs" / "rules.md").write_text(render())
    print(f"wrote docs/rules.md ({len(RULES)} rules)")
