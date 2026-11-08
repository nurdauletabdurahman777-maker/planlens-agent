"""Render one recorded check as a self-contained HTML report.

Everything comes from :func:`planlens_agent.view_model.build_view`, which is
built from the ``CheckResult``. All content is rendered here as escaped HTML,
so every finding is readable with scripting disabled; the bundled script only
highlights what a selected finding points at.

The page loads nothing: styles and script are inlined, and a
Content-Security-Policy allows only those exact blocks (by hash) and forbids
every network fetch. Every input-derived string is escaped for HTML, and the
embedded view data is escaped so that no input can close its ``<script>``.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
from importlib import resources
from typing import Any

from ..report import safe

DIAGRAM_MAX_STEPS = 150
DIAGRAM_MAX_ARCS = 1500
COMPACT_ITEMS = 6
_VERDICT_LABEL = {"pass": "PASS", "reject": "REJECTED", "error": "ERROR"}

_CATEGORY_LABEL = {
    "violation": ("Violation", "violation"),
    "invalid_plan": ("Invalid plan", "invalid"),
    "invalid_contract": ("Invalid contract", "invalid"),
    "invalid_catalog": ("Invalid catalog", "invalid"),
    "unsupported": ("Cannot evaluate", "unsupported"),
}
_ERROR_KIND_TEXT = {
    "invalid_plan": "the candidate plan is not a valid plan document",
    "invalid_contract": "the owner contract is invalid",
    "invalid_catalog": "the action catalog is invalid",
    "unsupported": "a required check or the plan's semantics cannot be evaluated by this engine",
}


def _asset(name: str) -> str:
    return resources.files(__name__).joinpath(name).read_text(encoding="utf-8")


def e(value: Any) -> str:
    """Escape any input-derived value for HTML text or a quoted attribute."""
    text = safe(str(value)).encode("utf-8", "backslashreplace").decode("utf-8")
    return html.escape(text, quote=True)


def e_block(value: Any) -> str:
    """Like :func:`e`, but keeps line breaks, for preformatted evidence."""
    return "\n".join(e(line) for line in str(value).split("\n"))


def _embed_json(data: Any) -> str:
    """JSON safe to place inside <script type="application/json">."""
    text = json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _label_cells(fragment: str) -> str:
    """Give each cell its column heading, for the stacked layout on narrow screens.

    Safe as a text pass because every input string is escaped before it gets here, so
    ``<table>``, ``<tr>`` and ``<td>`` only ever appear as real markup.
    """

    def table(m: re.Match[str]) -> str:
        heads = re.findall(r'<th scope="col">(.*?)</th>', m.group(0))

        def row(r: re.Match[str]) -> str:
            cells = iter(heads)
            return re.sub(
                r"<td(?![^>]*colspan)",
                lambda _: f'<td data-label="{next(cells, "")}"',
                r.group(0),
            )

        return re.sub(r"<tr.*?</tr>", row, m.group(0), flags=re.S)

    return re.sub(r"<table>.*?</table>", table, fragment, flags=re.S)


def _csp_hash(text: str) -> str:
    return (
        "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode() + "'"
    )


def _short(digest: str | None) -> str:
    return digest[:12] if digest else "-"


def _pretty(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)


def _glyph(verdict: str) -> str:
    if verdict == "pass":
        shape = (
            '<circle cx="14" cy="14" r="12" fill="none" stroke="currentColor" stroke-width="2"/>'
            '<path d="M8 14.5l4 4 8-9" fill="none" stroke="currentColor" stroke-width="2.4"/>'
        )
    elif verdict == "reject":
        shape = (
            '<path d="M9 3h10l6 6v10l-6 6H9l-6-6V9z" fill="none" stroke="currentColor" '
            'stroke-width="2"/><path d="M10 10l8 8M18 10l-8 8" stroke="currentColor" '
            'stroke-width="2.4"/>'
        )
    else:
        shape = (
            '<path d="M14 3l12 21H2z" fill="none" stroke="currentColor" stroke-width="2"/>'
            '<path d="M14 11v6M14 20v1.5" stroke="currentColor" stroke-width="2.4"/>'
        )
    return f'<svg class="glyph" viewBox="0 0 28 28" aria-hidden="true">{shape}</svg>'


def _verdict_block(v: dict[str, Any]) -> str:
    n = len(v["diagnostics"])
    if v["verdict"] == "pass":
        label = "PASS"
        answer = "Yes. The candidate satisfies every supported, required declared check."
    elif v["verdict"] == "reject":
        label = "REJECTED"
        answer = (
            f"No. The candidate did not pass the declared checks: "
            f"{n} violation{'s' if n != 1 else ''}."
        )
    else:
        label = "ERROR"
        answer = (
            "Not determined. The checks could not be fully evaluated because "
            f"{_ERROR_KIND_TEXT.get(v['error_kind'] or '', 'of an input problem')}."
        )
    return (
        f'<div class="verdict {e(v["verdict"])}" role="status">{_glyph(v["verdict"])}'
        f'<div><div class="label">{label} &middot; exit {v["exit_code"]}</div>'
        f'<div class="answer">{e(answer)}</div></div></div>'
    )


def _identity(v: dict[str, Any]) -> str:
    ident = v["identity"]
    plan = ident["plan"]
    rows = [
        (
            "Plan",
            f'<span class="mono">{e(plan["origin"])}</span><br>'
            f'<span class="muted mono">sha256:{e(_short(plan["sha256"]))}'
            f"{' &middot; ' + e(plan['steps']) + ' steps' if plan['steps'] is not None else ''}"
            f"</span>",
        )
    ]
    for key, title in (("contract", "Contract"), ("catalog", "Catalog")):
        doc = ident[key]
        rows.append(
            (
                title,
                (
                    f'{e(doc["id"])} {e(doc["version"])}<br><span class="muted mono">'
                    f"sha256:{e(_short(doc['sha256']))} &middot; {e(doc['origin'])}</span>"
                )
                if doc
                else f'<span class="muted">could not be used</span><br><span class="muted mono">'
                f"{e(v['unusable_documents'][key] or '-')}</span>",
            )
        )
    rows.append(("Engine", f"planlens-agent {e(v['engine']['version'])}"))
    cells = "".join(f"<div><dt>{t}</dt><dd>{body}</dd></div>" for t, body in rows)
    return f'<dl class="identity">{cells}</dl>'


def _where(d: dict[str, Any]) -> str:
    loc = d["location"]
    if loc is None:
        return f"No plan location: {e(d['location_absent_reason'] or 'none recorded')}"
    parts = []
    if loc["step_index"] is not None:
        parts.append(f"steps[{loc['step_index']}]")
    if loc["step_id"]:
        parts.append(e(loc["step_id"]))
    parts.append(f"{e(loc['document'])}:{e(loc['path'] or '/')}")
    if loc["source_span"]:
        parts.append(f"line {loc['source_span']['line']}, column {loc['source_span']['column']}")
    return " &middot; ".join(parts)


def _compact(value: Any) -> str:
    """A short, escaped summary of a structured value, for reading at a glance.

    Works only from the value's structure, so it applies to any finding and never
    interprets message wording. The complete value stays in the raw disclosure.
    """
    if value is None:
        return ""
    if isinstance(value, dict):
        if "step_index" in value:
            text = f"steps[{e(value['step_index'])}] {e(value.get('step_id', ''))}"
            if "input" in value:
                text += f" input {e(value['input'])}"
            if "refers_to" in value:
                text += f" &rarr; {e(value['refers_to'])}"
            return text
        parts = [f"{e(k.replace('_', ' '))}: {_compact(v) or 'none'}" for k, v in value.items()]
        return _limited(parts, "; ") or "none"
    if isinstance(value, (list, tuple)):
        lines = any(isinstance(item, dict) for item in value)
        return _limited([_compact(item) for item in value], "<br>" if lines else ", ") or "none"
    return e(value)


def _limited(parts: list[str], separator: str) -> str:
    shown = separator.join(parts[:COMPACT_ITEMS])
    extra = len(parts) - COMPACT_ITEMS
    return f"{shown}{separator}and {extra} more" if extra > 0 else shown


def _finding(d: dict[str, Any]) -> str:
    label, cls = _CATEGORY_LABEL[d["category"]]
    because = d["evidence"].get("because") if isinstance(d["evidence"], dict) else None
    related = [
        f"<li><code>{e(r['document'])}:{e(r['path'] or '/')}</code>"
        f"{' (' + e(r['step_id']) + ')' if r['step_id'] else ''}</li>"
        for r in d["related_locations"]
    ]
    rows = []
    if d["observed"] is not None:
        rows.append(("Observed", _compact(d["observed"])))
    if d["required"] is not None:
        rows.append(("Required", _compact(d["required"])))
    if d["contract_rule"]:
        rows.append(("Contract rule", f"<code>{e(d['contract_rule'])}</code>"))
    if because:
        rows.append(("Why", e(because)))
    rows.append(("Rule", f"{e(d['title'])} &middot; rule version {d['rule_version']}"))
    rows.append(("Meaning", e(d["explanation"])))
    if d["suggestion"]:
        rows.append(("Next", e(d["suggestion"])))
    if related:
        items = f'<ul class="notchecked flush">{"".join(related)}</ul>'
        if len(related) > COMPACT_ITEMS:
            items = f"<details><summary>{len(related)} related locations</summary>{items}</details>"
        rows.append(("Also see", items))
    rows.append(("Offline help", f"<code>planlens-agent explain {e(d['rule_id'])}</code>"))
    dl = "".join(f"<dt>{k}</dt><dd>{val}</dd>" for k, val in rows)
    raw = ""
    if isinstance(d["observed"], (dict, list)) or isinstance(d["required"], (dict, list)):
        raw = (
            '<details class="raw"><summary>Raw observed and required data</summary>'
            f'<div class="compare"><div><h4>Observed</h4><pre>{e_block(_pretty(d["observed"]))}</pre></div>'
            f"<div><h4>Required</h4><pre>{e_block(_pretty(d['required']))}</pre></div></div></details>"
        )
    return (
        f'<li class="finding" id="finding-{e(d["id"])}">'
        f'<button type="button" class="select" data-finding-select="{e(d["id"])}" aria-pressed="false" '
        f'aria-describedby="details-{e(d["id"])}">'
        f'<span class="tag {cls}">{label}</span><span class="rule-id">{e(d["rule_id"])}</span>'
        f'<span class="msg">{e(d["message"])}</span><span class="where">{_where(d)}</span></button>'
        f'<div class="details"><dl id="details-{e(d["id"])}">{dl}</dl>{raw}</div></li>'
    )


def _findings_panel(v: dict[str, Any]) -> str:
    ds = v["diagnostics"]
    if ds:
        body = (
            f'<p class="sr-only">Select a finding to highlight the steps, rules and artifact '
            f"references it involves. Arrow keys move between findings; Escape clears.</p>"
            f'<ol class="findings">{"".join(_finding(d) for d in ds)}</ol>'
        )
    else:
        body = (
            '<p class="empty">No findings. Every supported required check in the contract '
            "and catalog is satisfied by the declared plan.</p>"
        )
    return (
        f'<section class="panel findings-col" id="findings" aria-labelledby="h-findings">'
        f'<header><h2 id="h-findings">Findings</h2><span class="muted">{len(ds)}</span></header>'
        f"{body}</section>"
    )


def _relation_count(v: dict[str, Any]) -> int:
    """How many arcs the diagram would draw, computed without building any of them."""
    prerequisites = sum(len(p["prior_steps"]) * len(p["action_steps"]) for p in v["prerequisites"])
    return prerequisites + sum(len(p["consumers"]) for p in v["artifacts"]["producers"])


def _diagram(v: dict[str, Any]) -> str:
    steps = v["plan"]["steps"]
    if len(steps) > DIAGRAM_MAX_STEPS:
        return (
            f'<p class="note">The diagram is omitted for plans over {DIAGRAM_MAX_STEPS} steps; '
            "the sequence table below lists every step.</p>"
        )
    # Budget first: counting is cheap, and building paths only to discard them is not.
    if _relation_count(v) > DIAGRAM_MAX_ARCS:
        return (
            '<p class="note">The diagram is omitted because this plan has too many '
            "relationships to draw legibly; the tables list all of them.</p>"
        )
    row, node_h, top = 44, 32, 14
    x0, w = 170, 420
    height = top * 2 + row * len(steps)

    def cy(i: int) -> float:
        return top + i * row + node_h / 2

    arcs = []
    for k, p in enumerate(v["prerequisites"]):
        for j in p["prior_steps"]:
            for i in p["action_steps"]:
                if i == j:
                    continue
                bulge = max(18.0, x0 - 24 - 16 * k - min(abs(i - j), 8) * 6)
                backward = " backward" if j > i else ""
                arcs.append(
                    f'<path class="arc pre{backward}" data-prereq="{e(p["rule_id"])}" '
                    f'd="M{x0},{cy(j)} C{bulge},{cy(j)} {bulge},{cy(i)} {x0},{cy(i)}">'
                    f"<title>{e(p['requires_prior'])} (steps[{j}]) before {e(p['action'])} "
                    f"(steps[{i}]), rule {e(p['rule_id'])}</title></path>"
                )
    xr = x0 + w
    for k, prod in enumerate(v["artifacts"]["producers"]):
        for c in prod["consumers"]:
            i, j = c["step_index"], prod["producer_index"]
            if i == j:
                continue
            bulge = xr + 26 + 14 * (k % 8) + min(abs(i - j), 8) * 4
            arcs.append(
                f'<path class="arc art" data-artifact="{e(prod["key"])}" '
                f'd="M{xr},{cy(j)} C{bulge},{cy(j)} {bulge},{cy(i)} {xr},{cy(i)}">'
                f"<title>{e(prod['key'])} from steps[{j}] used by steps[{i}] "
                f"input {e(c['input'])}</title></path>"
            )
    badges: dict[int, list[str]] = {}
    for g in v["same_artifact"]:
        for b in g["bindings"]:
            if b["group"] and b["group"] not in badges.setdefault(b["step_index"], []):
                badges[b["step_index"]].append(b["group"])
    nodes = []
    for s in steps:
        y = top + s["index"] * row
        label = f"{s['id']} · {s['action']}"
        room = 46 - sum(len(g) + 2 for g in badges.get(s["index"], []))
        shown = label if len(label) <= room else label[: room - 1] + "…"
        cls = (
            "node"
            + ("" if s["action_known"] else " unknown")
            + (" flagged" if s["finding_ids"] else "")
        )
        flag = (
            f'<rect class="flagmark" x="{x0 + w - 14}" y="{y + 11}" width="8" height="10" rx="1"/>'
            if s["finding_ids"]
            else ""
        )
        nodes.append(
            f'<g class="{cls}" data-step="{s["index"]}"><title>steps[{s["index"]}] {e(label)}'
            f"{' (action not in catalog)' if not s['action_known'] else ''}"
            f"{' - has findings' if s['finding_ids'] else ''}</title>"
            f'<rect class="box" x="{x0}" y="{y}" width="{w}" height="{node_h}" rx="4"/>'
            f'<text class="idx" x="{x0 + 10}" y="{y + 21}">{s["index"]}</text>'
            f'<text x="{x0 + 40}" y="{y + 21}">{e(shown)}</text>{flag}'
            f"{_badges(badges.get(s['index'], []), x0 + w - 24, y)}</g>"
        )
    total_w = xr + 150
    summary = (
        f"Proposed sequence of {len(steps)} steps, with prerequisite relations on the left "
        "and artifact references on the right. The table below lists the same information."
    )
    svg = (
        f'<svg class="diagram" viewBox="0 0 {total_w} {height}" role="img" '
        f'aria-label="{e(summary)}">'
        f'<text class="lane-label" x="8" y="10">prerequisites</text>'
        f'<text class="lane-label" x="{xr + 12}" y="10">artifact references</text>'
        f"{''.join(arcs)}{''.join(nodes)}</svg>"
    )
    keys = []
    if any(p["prior_steps"] and p["action_steps"] for p in v["prerequisites"]):
        keys.append(
            '<span><svg width="34" height="10"><path d="M2 5h30" stroke="currentColor" '
            'stroke-width="1.6"/></svg>prerequisite step listed earlier</span>'
        )
    if 'class="arc pre backward"' in "".join(arcs):
        keys.append(
            '<span><svg width="34" height="10"><path d="M2 5h30" stroke="currentColor" '
            'stroke-width="1.6" stroke-dasharray="5 4"/></svg>prerequisite step listed later</span>'
        )
    if any(s["finding_ids"] for s in steps):
        keys.append(
            '<span><svg width="14" height="12"><rect x="2" y="1" width="8" height="10" rx="1" '
            'fill="currentColor"/></svg>step with findings</span>'
        )
    if any(d["focus"]["producer_steps"] for d in v["diagnostics"]):
        keys.append(
            '<span><span class="producer-key"></span>produces an artifact the selected '
            "finding compares</span>"
        )
    if any(not s["action_known"] for s in steps):
        keys.append(
            '<span><svg width="30" height="14"><rect x="1" y="1" width="28" height="12" rx="2" '
            'fill="none" stroke="currentColor" stroke-dasharray="4 3"/></svg>action not in '
            "catalog</span>"
        )
    legend = f'<div class="legend">{"".join(keys)}</div>'
    region = (
        '<div class="diagramwrap" role="region" tabindex="0" '
        'aria-label="Sequence diagram. Scrolls sideways on narrow screens."'
    )
    return f"{region}>{svg}</div>{legend}"


def _badges(groups: list[str], right: int, y: int) -> str:
    """Same-artifact group letters for one step, drawn inside its node."""
    out = []
    x = right
    for g in reversed(groups):
        cls = f"grp-{g}" if len(g) == 1 and g in "ABCD" else "grp-other"
        width = 9 + 7 * len(g)  # labels grow past Z: AA, AAA
        x -= width + 4
        out.append(
            f'<g class="badge"><title>same-artifact group {e(g)}</title><rect class="{cls}" '
            f'x="{x}" y="{y + 8}" width="{width}" height="16" rx="2"/>'
            f'<text x="{x + width / 2}" y="{y + 20}">{e(g)}</text></g>'
        )
    return "".join(out)


def _chip(ref: str, group: str | None = None) -> str:
    grp = ""
    if group:
        cls = f"grp-{group}" if group in "ABCD" and len(group) == 1 else "grp-other"
        grp = f'<span class="grp {cls}" aria-label="group {e(group)}">{e(group)}</span>'
    return f'<span class="chip" data-artifact="{e(ref)}">{grp}{e(ref)}</span>'


def _sequence_panel(v: dict[str, Any]) -> str:
    plan = v["plan"]
    group_of: dict[tuple[int, str], str] = {}
    for g in v["same_artifact"]:
        for b in g["bindings"]:
            if b["group"]:
                group_of[(b["step_index"], b["input"])] = b["group"]
    rows = []
    for s in plan["steps"]:
        inputs = (
            "".join(
                _chip(i["ref"], group_of.get((s["index"], i["name"])))
                + f'<span class="muted"> &larr; {e(i["name"])}'
                + (
                    f", from steps[{i['producer_index']}]"
                    if i["producer_index"] is not None
                    else ", no step with this id"
                )
                + "</span><br>"
                for i in s["inputs"]
            )
            or '<span class="muted">-</span>'
        )
        links = (
            ", ".join(
                f'<a href="#finding-{e(f)}" data-goto-finding="{e(f)}">{e(f)}</a>'
                for f in s["finding_ids"]
            )
            or '<span class="muted">-</span>'
        )
        action = e(s["action"]) + (
            "" if s["action_known"] else ' <span class="muted">(not in catalog)</span>'
        )
        desc = f'<br><span class="muted">{e(s["description"])}</span>' if s["description"] else ""
        rows.append(
            f'<tr data-step="{s["index"]}"><td class="num">{s["index"]}</td>'
            f'<td class="mono">{e(s["id"])}{desc}</td><td>{action}</td>'
            f"<td>{inputs}</td><td>{links}</td></tr>"
        )
    title = ""  # the plan title is already the page heading
    table = (
        '<div class="tablewrap"><table><caption class="sr-only">Steps in proposed execution '
        "order. The position is the step index in the plan, not a line number.</caption>"
        '<thead><tr><th scope="col">Position</th><th scope="col">Step id</th>'
        '<th scope="col">Action</th><th scope="col">Inputs (artifact references)</th>'
        f'<th scope="col">Findings</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    )
    return (
        f'<section class="panel" aria-labelledby="h-seq"><header><h2 id="h-seq">Proposed '
        f'sequence</h2><span class="muted">list order is execution order</span></header>'
        f'<div class="body">{title}{_diagram(v)}<div class="mt16">{table}</div>'
        f"</div></section>"
    )


def _requirements_panel(v: dict[str, Any]) -> str:
    req_rows = []
    for r in v["required_actions"]:
        where = (
            ", ".join(f"steps[{i}]" for i in r["step_indices"])
            if r["present"]
            else "no step performs this action"
        )
        status = (
            '<span class="status-present">present</span>'
            if r["present"]
            else '<span class="status-absent">absent</span>'
        )
        req_rows.append(
            f'<tr data-required="{e(r["action"])}"><td class="mono">{e(r["action"])}</td>'
            f"<td>{status}</td><td>{where}</td></tr>"
        )
    pre_rows = []
    for p in v["prerequisites"]:
        prior = ", ".join(f"steps[{i}]" for i in p["prior_steps"]) or "none in the plan"
        act = ", ".join(f"steps[{i}]" for i in p["action_steps"]) or "none in the plan"
        because = f'<br><span class="muted">{e(p["because"])}</span>' if p["because"] else ""
        pre_rows.append(
            f'<tr data-prereq="{e(p["rule_id"])}"><td class="mono">{e(p["rule_id"])}</td>'
            f"<td>every <code>{e(p['action'])}</code> needs an earlier "
            f"<code>{e(p['requires_prior'])}</code>{because}</td>"
            f"<td>{e(p['requires_prior'])}: {prior}<br>{e(p['action'])}: {act}</td></tr>"
        )
    req = (
        f'<h3>Required actions</h3><div class="tablewrap"><table><thead><tr><th scope="col">Action'
        f'</th><th scope="col">In the plan</th><th scope="col">Steps</th></tr></thead><tbody>'
        f"{''.join(req_rows) or '<tr><td colspan=3>None required.</td></tr>'}</tbody></table></div>"
    )
    pre = (
        f'<h3 class="mt18">Prerequisites</h3><div class="tablewrap"><table><thead><tr>'
        f'<th scope="col">Rule</th><th scope="col">Requirement</th><th scope="col">Where in the plan'
        f"</th></tr></thead><tbody>"
        f"{''.join(pre_rows) or '<tr><td colspan=3>No prerequisite rules.</td></tr>'}"
        f"</tbody></table></div>"
    )
    return (
        f'<section class="panel" aria-labelledby="h-req"><header><h2 id="h-req">Requirements'
        f'</h2><span class="muted">from the owner contract</span></header>'
        f'<div class="body">{req}{pre}</div></section>'
    )


def _artifacts_panel(v: dict[str, Any]) -> str:
    prod_rows = []
    for p in v["artifacts"]["producers"]:
        cons = (
            "".join(
                f'steps[{c["step_index"]}] <span class="mono">{e(c["step_id"])}</span> '
                f'<span class="muted">input {e(c["input"])}</span><br>'
                for c in p["consumers"]
            )
            or '<span class="muted">not used</span>'
        )
        prod_rows.append(
            f'<tr data-artifact="{e(p["key"])}"><td>{_chip(p["key"])}<br>'
            f'<span class="muted">from steps[{p["producer_index"]}]</span></td>'
            f'<td class="mono">{e(p["type"])}</td><td>{cons}</td></tr>'
        )
    for u in v["artifacts"]["unresolved"]:
        cons = "".join(
            f'steps[{c["step_index"]}] <span class="mono">{e(c["step_id"])}</span> '
            f'<span class="muted">input {e(c["input"])}</span><br>'
            for c in u["consumers"]
        )
        prod_rows.append(
            f'<tr data-artifact="{e(u["ref"])}"><td>{_chip(u["ref"])}<br><span class='
            f'"muted">no matching producer output in the plan</span></td>'
            f'<td class="muted">-</td><td>{cons}</td></tr>'
        )
    producers = (
        f'<h3>Producer outputs and their consumers</h3><div class="tablewrap"><table><thead>'
        f'<tr><th scope="col">Planned artifact</th><th scope="col">Type</th><th scope="col">'
        f"Used by</th></tr></thead><tbody>"
        f"{''.join(prod_rows) or '<tr><td colspan=3>No artifact outputs.</td></tr>'}"
        f"</tbody></table></div>"
    )
    same = []
    for g in v["same_artifact"]:
        rows = "".join(
            f'<tr data-step="{b["step_index"]}"><td class="num">steps[{b["step_index"]}]</td>'
            f'<td class="mono">{e(b["step_id"])}</td><td class="mono">{e(b["action"])}.'
            f"{e(b['input'])}</td><td>"
            + (_chip(b["ref"], b["group"]) if b["ref"] else '<span class="muted">not bound</span>')
            + "</td></tr>"
            for b in g["bindings"]
        )
        groups = "; ".join(
            f"group {e(x['label'])} = <code>{e(x['ref'])}</code> "
            f"({x['bindings']} binding{'s' if x['bindings'] != 1 else ''})"
            for x in g["groups"]
        )
        if g["finding_ids"]:
            verdict_note = (
                "The checker reports that these bindings do not all refer to one "
                "planned artifact. The groups show which bindings agree; the checker "
                "cannot tell which group is the wrong one."
            )
        else:
            verdict_note = "No finding was reported for this rule."
        because = f'<p class="muted">{e(g["because"])}</p>' if g["because"] else ""
        same.append(
            f'<div data-same="{e(g["rule_id"])}" class="mt18"><h3>Same planned artifact: '
            f'<code>{e(g["rule_id"])}</code></h3>{because}<p class="note">Compared inputs: '
            f"{', '.join(f'<code>{e(m)}</code>' for m in g['members'])}. "
            f"{groups or 'No bindings in the plan.'}</p>"
            f'<div class="tablewrap"><table><thead><tr><th scope="col">Position</th><th scope="col">'
            f'Step id</th><th scope="col">Input</th><th scope="col">Refers to</th></tr></thead>'
            f"<tbody>{rows or '<tr><td colspan=4>None of these actions appear.</td></tr>'}</tbody>"
            f'</table></div><p class="note">{e(verdict_note)}</p></div>'
        )
    return (
        f'<section class="panel" aria-labelledby="h-art"><header><h2 id="h-art">Artifacts</h2>'
        f'<span class="muted">planned producer and output, not bytes</span></header>'
        f'<div class="body">{producers}{"".join(same)}</div></section>'
    )


def _not_evaluated_panel(v: dict[str, Any]) -> str:
    kind = _ERROR_KIND_TEXT.get(v["error_kind"] or "", "of an input problem")
    return (
        f'<section class="panel" aria-labelledby="h-ne"><header><h2 id="h-ne">Plan not evaluated'
        f'</h2></header><div class="body"><p>The checker stopped before evaluating the plan '
        f"because {e(kind)}. No sequence, requirement or artifact view is shown: the "
        f"inspector does not reconstruct a plan or policy that the checker could not use. The "
        f"findings list everything that was recorded.</p></div></section>"
    )


def render_html(view: dict[str, Any]) -> str:
    css, js = _asset("inspector.css"), _asset("inspector.js")
    plan = view["plan"]
    origin = view["identity"]["plan"]["origin"]
    heading = (plan or {}).get("title") or origin
    if plan is not None:
        right = _label_cells(
            _sequence_panel(view) + _requirements_panel(view) + _artifacts_panel(view)
        )
    else:
        right = _not_evaluated_panel(view)
    notchecked = "".join(f"<li>{e(x)}</li>" for x in view["not_checked"])
    csp = (
        f"default-src 'none'; style-src {_csp_hash(css)}; script-src {_csp_hash(js)}; "
        "img-src 'none'; base-uri 'none'; form-action 'none'"
    )
    alt = (
        f"<code>planlens-agent check {e(origin)} --format json</code> and "
        f"<code>--format sarif</code> give the same result as JSON and SARIF."
    )
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
        '<meta name="referrer" content="no-referrer">'
        f"<title>PlanLens report - {_VERDICT_LABEL[view['verdict']]} - {e(origin)}</title>"
        f"<style>{css}</style></head><body>"
        '<a class="sr-only skip" href="#findings">Skip to findings</a><div class="wrap">'
        '<header class="top"><div class="minw0">'
        '<p class="eyebrow">PlanLens &middot; declared-plan check report</p>'
        f'<h1 class="wrapany">{e(heading)}</h1></div>{_verdict_block(view)}</header>'
        f"{_identity(view)}"
        f'<p class="scope"><strong>Scope:</strong> {e(view["scope"])}. This page records one '
        "check. To change the result, edit the plan and run the check again; the page does "
        "not re-evaluate anything.</p>"
        '<p class="sr-only" id="selection-status" aria-live="polite"></p>'
        f'<div class="grid">{_findings_panel(view)}<div class="stack">{right}</div></div>'
        '<section class="panel mt24" aria-labelledby="h-nc"><header>'
        '<h2 id="h-nc">Not checked by this tool</h2></header><div class="body">'
        f'<ul class="notchecked">{notchecked}</ul></div></section>'
        f'<footer class="alt"><p>Text, JSON and SARIF alternatives: {alt}</p>'
        f"<noscript><p>Scripting is off: every finding is shown above; selecting a finding "
        "to highlight it needs scripting.</p></noscript></footer></div>"
        f'<script type="application/json" id="planlens-view">{_embed_json(view)}</script>'
        f"<script>{js}</script></body></html>\n"
    )
