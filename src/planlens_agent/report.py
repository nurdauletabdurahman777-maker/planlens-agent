"""Rendering a check result as a JSON report or as plain text.

Both renderings are built from the same :class:`~planlens_agent.checker.CheckResult`
and the same diagnostics; the text form only groups and formats them.

Text output is ASCII in its fixed parts and uses no colour or ANSI escapes.
Strings that come from inputs - step ids, descriptions, the owner's "because"
text - have control and formatting characters escaped before printing, so a
plan cannot inject terminal escape sequences or bidirectional overrides.
JSON reports are written with ``ensure_ascii=True`` for the same reason.
"""

from __future__ import annotations

import json
import unicodedata
from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Any

from ._version import __version__

if TYPE_CHECKING:
    from .checker import CheckResult
    from .diagnostics import Diagnostic


def safe(text: str) -> str:
    """Escape control (Cc) and format (Cf) characters for terminal display."""
    out = []
    for ch in text:
        if unicodedata.category(ch) in ("Cc", "Cf"):
            out.append(f"\\x{ord(ch):02x}" if ord(ch) < 0x100 else f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def report_dict(result: CheckResult) -> dict[str, Any]:
    from .checker import NOT_CHECKED, REPORT_SCHEMA, SCOPE

    pol = result.policy
    located = [
        d.location
        for d in result.diagnostics
        if d.location is not None and d.location.step_index is not None
    ]
    earliest = min(located, key=lambda loc: loc.step_index or 0) if located else None
    return {
        "report_schema": REPORT_SCHEMA,
        "engine": {"name": "planlens-agent", "version": __version__},
        "scope": SCOPE,
        "verdict": result.verdict,
        "exit_code": result.exit_code,
        "error_kind": result.error_kind,
        "passes_required_checks": result.passed,
        "plan_evaluated": result.plan_evaluated,
        "inputs": {
            "plan": {
                "origin": result.plan_origin,
                "sha256": result.plan_digest,
                "steps": result.step_count,
            },
            "contract": (
                {
                    "origin": pol.contract.origin,
                    "id": pol.contract.id,
                    "version": pol.contract.version,
                    "sha256": pol.contract.digest,
                }
                if pol.contract
                else None
            ),
            "catalog": (
                {
                    "origin": pol.catalog.origin,
                    "id": pol.catalog.id,
                    "version": pol.catalog.version,
                    "sha256": pol.catalog.digest,
                }
                if pol.catalog
                else None
            ),
        },
        "summary": {
            "findings": len(result.diagnostics),
            "by_category": dict(sorted(Counter(d.category for d in result.diagnostics).items())),
            "by_rule": dict(sorted(Counter(d.rule_id for d in result.diagnostics).items())),
            "earliest_step_with_finding": (
                {"step_index": earliest.step_index, "step_id": earliest.step_id}
                if earliest
                else None
            ),
            "earliest_step_note": "the earliest plan position with a finding; not a proven cause",
        },
        "diagnostics": [d.to_dict() for d in result.diagnostics],
        "not_checked": list(NOT_CHECKED),
    }


def _compact(value: Any) -> str:
    if isinstance(value, str):
        return safe(value)
    return safe(json.dumps(value, ensure_ascii=False, sort_keys=True))


_HEADLINE = {"pass": "PASS", "reject": "REJECTED", "error": "ERROR"}


def render_text(result: CheckResult) -> str:
    from .checker import NOT_CHECKED

    pol = result.policy
    lines: list[str] = []
    lines.append(f"planlens-agent {__version__} - declared-plan check")
    lines.append(
        f"  plan      {safe(result.plan_origin)}"
        + (f"  sha256:{result.plan_digest[:12]}" if result.plan_digest else "")
    )
    if pol.contract:
        lines.append(
            f"  contract  {pol.contract.id} {pol.contract.version}  "
            f"sha256:{pol.contract.digest[:12]}"
        )
    if pol.catalog:
        lines.append(
            f"  catalog   {pol.catalog.id} {pol.catalog.version}  sha256:{pol.catalog.digest[:12]}"
        )
    lines.append("")

    n = len(result.diagnostics)
    head = f"RESULT: {_HEADLINE[result.verdict]} (exit {result.exit_code})"
    if result.verdict == "pass":
        head += f" - {result.step_count} steps satisfy every supported required check"
    elif result.verdict == "reject":
        head += f" - {n} violation{'s' if n != 1 else ''}"
    else:
        head += f" - {result.error_kind}; {n} finding{'s' if n != 1 else ''}"
    lines.append(head)
    if not result.plan_evaluated and result.verdict == "error":
        lines.append("  The plan was not evaluated. Fix the input or policy first.")

    by_step: dict[int, list[Diagnostic]] = defaultdict(list)
    other: list[Diagnostic] = []
    for d in result.diagnostics:
        if d.location is not None and d.location.step_index is not None:
            by_step[d.location.step_index].append(d)
        else:
            other.append(d)

    for idx in sorted(by_step):
        group = by_step[idx]
        sid = group[0].location.step_id if group[0].location else None
        lines.append("")
        lines.append(f"steps[{idx}]" + (f"  id={safe(sid)}" if sid else ""))
        for d in group:
            lines.extend(_entry(d))
    if other:
        lines.append("")
        lines.append("not tied to a plan step")
        for d in other:
            lines.extend(_entry(d))

    lines.append("")
    lines.append("Not checked by this tool:")
    lines.extend(f"  - {item}" for item in NOT_CHECKED)
    if result.diagnostics:
        lines.append(
            f"Explain a rule offline: planlens-agent explain {result.diagnostics[0].rule_id}"
        )
    return "\n".join(lines) + "\n"


def _entry(d: Diagnostic) -> list[str]:
    tag = f"  [{d.category}] {d.rule_id}"
    if d.contract_rule:
        tag += f"  (contract rule: {safe(d.contract_rule)})"
    out = [tag, f"    {safe(d.message)}"]
    if d.observed is not None:
        out.append(f"    observed: {_compact(d.observed)}")
    if d.required is not None:
        out.append(f"    required: {_compact(d.required)}")
    if d.location is not None:
        where = f"{d.location.document}:{d.location.path or '/'}"
        if d.location.source_span is not None:
            where += (
                f" (line {d.location.source_span.line}, column {d.location.source_span.column})"
            )
        out.append(f"    at:       {where}")
    elif d.location_absent_reason:
        out.append(f"    at:       no location - {d.location_absent_reason}")
    for rel in d.related:
        out.append(f"    see:      {rel.document}:{rel.path}")
    because = d.evidence.get("because") if d.evidence else None
    if because:
        out.append(f"    why:      {safe(str(because))}")
    if d.suggestion:
        out.append(f"    next:     {safe(d.suggestion)}")
    return out
