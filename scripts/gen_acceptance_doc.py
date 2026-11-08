"""Write docs/acceptance-examples.md from tests/acceptance/cases.toml."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def render() -> str:
    data = tomllib.loads((ROOT / "tests" / "acceptance" / "cases.toml").read_text())
    lines = [
        "# Acceptance examples",
        "",
        "Generated from `tests/acceptance/cases.toml` by `scripts/gen_acceptance_doc.py`.",
        "",
        "These are **authored software fixtures** covering the Open-Source Standard,",
        "section 7. They test that the checker behaves as specified on cases written",
        "for that purpose. They are not observations of any agent or model, and they",
        "say nothing about how often real plans contain these problems.",
        "",
        "Each case runs through the SDK and the CLI, which must agree exactly",
        "(`tests/test_acceptance.py`). Unless stated, the policy is the bundled",
        "`release-basic` contract and catalog.",
        "",
        "| # | case | input | policy | verdict | findings (exact counts) | location |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for c in data["cases"]:
        plan = c["plan"].split("/")[-1]
        policy = c.get("contract", "release-basic").split("/")[-1]
        rules = ", ".join(f"`{r}` x{n}" for r, n in c["rules"].items()) or "none"
        locs = [
            f"`{loc['path']}`" + (f" ({loc['step_id']})" if "step_id" in loc else "")
            for loc in c.get("locations", [])
        ]
        locs += [f"`{r}`: none, by design" for r in c.get("absent", [])]
        if "source_span" in c:
            locs.append(f"line {c['source_span']['line']}, column {c['source_span']['column']}")
        verdict = c["verdict"] + (f" ({c['error_kind']})" if "error_kind" in c else "")
        lines.append(
            f"| {c['standard']} | {c['name']} | `{plan}` | `{policy}` | {verdict} "
            f"| {rules} | {'; '.join(locs) or '-'} |"
        )
    lines += [
        "| 9 | plan changes after validation | runner tests | release-basic | blocked "
        "| `DispatchBlocked`, zero handlers entered | - |",
        "",
        "Example 9 needs an execution step, so it lives in `tests/test_runner.py`:",
        "a payload whose artifact reference changed after approval is blocked, a",
        "changed payload is blocked even when it would pass on its own, and a",
        "mutation of the caller's original object after approval does not change",
        "what runs.",
        "",
        "Negative controls: the unsupported-rule case passes once that rule is",
        "removed, and removing the waiver from the policy-override case changes",
        "none of the requirements found.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    (ROOT / "docs" / "acceptance-examples.md").write_text(render())
    print("wrote docs/acceptance-examples.md")
