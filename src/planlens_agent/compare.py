"""Compare two saved runs: what changed between a baseline and a candidate?

Findings are matched by identity, never by position, so moving a step does not turn an
unchanged finding into a resolved one and a new one:

* within a case and sample, the identity is the rule, the contract rule and an anchor;
* the anchor is the step id and field for a plan finding, the document path for a policy
  finding, or the absent requirement for something with no location. A ``same_artifact``
  rule yields at most one finding, so its contract rule alone identifies it.

Where one identity occurs more than once, findings are paired by identical evidence
first. Anything left is paired only when exactly one remains on each side; otherwise it
is reported as ambiguous and the comparison is incomplete. Nothing is guessed.

Outcomes:

=====================  ====  ==============================================================
status                 exit  meaning
=====================  ====  ==============================================================
no_new_violations      0     like for like, every case accounted for, nothing new
new_violations         1     like for like, and the candidate has new findings
incomplete_comparison  2     cases removed or not evaluated, or findings ambiguous
not_comparable         2     policy, evaluator or suite differ, or the report schema is not
                             supported, so a difference in findings cannot be attributed to
                             the candidate plans
=====================  ====  ==============================================================

"No new violations" is about change. It is not an approval, and it says nothing about
whether the candidate passes: two identical rejected runs have no new violations.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any

from ._version import __version__
from .checker import REPORT_SCHEMA
from .report import safe
from .suite import NOT_AN_APPROVAL, RunFile, case_label

DIFF_SCHEMA = "planlens-agent/run-diff/v1"

_STEP_PATH = re.compile(r"^/steps/\d+(.*)$")
#: Evidence that describes where things sit in the plan, not what the finding says.
#: ``step_ids`` lists the whole plan, so it changes when any unrelated step is added.
_POSITION_KEYS = {"step_index", "consumer_index", "producer_index", "step_ids"}

#: Rules that produce at most one finding per contract rule, whichever step it points at.
_ONE_PER_CONTRACT_RULE = {"plan/artifact-identity"}

EXIT = {
    "no_new_violations": 0,
    "new_violations": 1,
    "incomplete_comparison": 2,
    "not_comparable": 2,
}


def _stable(location: dict[str, Any]) -> list[Any]:
    """A location without its position in the plan."""
    match = _STEP_PATH.match(location["path"] or "")
    if location["document"] == "plan" and match and location["step_id"]:
        return ["step", location["step_id"], match.group(1)]
    return [location["document"], location["path"]]


def finding_identity(f: dict[str, Any]) -> str:
    if f["rule_id"] in _ONE_PER_CONTRACT_RULE and f["contract_rule"]:
        anchor: list[Any] = ["rule"]
    elif f["location"] is None:
        anchor = ["absent", f["required"]]
    else:
        anchor = _stable(f["location"])
    return json.dumps([f["rule_id"], f["contract_rule"], anchor], sort_keys=True)


def _without_positions(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _without_positions(v) for k, v in value.items() if k not in _POSITION_KEYS}
    if isinstance(value, list):  # plan order is position too
        items = [_without_positions(v) for v in value]
        return sorted(items, key=lambda v: json.dumps(v, sort_keys=True))
    return value


def finding_evidence(f: dict[str, Any]) -> str:
    """What the finding says, with plan positions removed."""
    basis = {
        "observed": _without_positions(f["observed"]),
        "required": _without_positions(f["required"]),
        "evidence": _without_positions(f["evidence"]),
        "related": sorted(json.dumps(_stable(r)) for r in f["related_locations"]),
    }
    return hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()


def _key(case: dict[str, Any]) -> tuple[str, str | None]:
    return (case["case_id"], case["sample"])


def _position(f: dict[str, Any]) -> int | None:
    return f["location"]["step_index"] if f["location"] else None


def _entry(case: dict[str, Any], f: dict[str, Any]) -> dict[str, Any]:
    loc = f["location"]
    return {
        "case_id": case["case_id"],
        "sample": case["sample"],
        "rule_id": f["rule_id"],
        "contract_rule": f["contract_rule"],
        "step_id": loc["step_id"] if loc else None,
        "position": _position(f),
        "message": f["message"],
        "observed": f["observed"],
        "required": f["required"],
    }


def _pair(
    case: dict[str, Any],
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    out: dict[str, list[dict[str, Any]]],
) -> None:
    groups: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = defaultdict(
        lambda: ([], [])
    )
    for f in before:
        groups[finding_identity(f)][0].append(f)
    for f in after:
        groups[finding_identity(f)][1].append(f)

    for old, new in groups.values():
        remaining = list(new)
        leftover: list[dict[str, Any]] = []
        for f in old:
            evidence = finding_evidence(f)
            twin = next((g for g in remaining if finding_evidence(g) == evidence), None)
            if twin is None:
                leftover.append(f)
                continue
            remaining.remove(twin)
            entry = _entry(case, twin)
            entry["baseline_position"] = _position(f)
            entry["moved"] = _position(f) != _position(twin)
            out["unchanged"].append(entry)
        if leftover and remaining:
            if len(leftover) == 1 and len(remaining) == 1:
                out["changed_evidence"].append(
                    {"baseline": _entry(case, leftover[0]), "candidate": _entry(case, remaining[0])}
                )
            else:
                out["ambiguous"].append(
                    {
                        "case_id": case["case_id"],
                        "sample": case["sample"],
                        "rule_id": old[0]["rule_id"],
                        "contract_rule": old[0]["contract_rule"],
                        "baseline": [_entry(case, f) for f in leftover],
                        "candidate": [_entry(case, f) for f in remaining],
                    }
                )
        else:
            out["resolved"] += [_entry(case, f) for f in leftover]
            out["new"] += [_entry(case, f) for f in remaining]


def _context_changes(a: dict[str, Any], b: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []

    def add(kind: str, old: Any, new: Any, why: str) -> None:
        changes.append({"kind": kind, "baseline": old, "candidate": new, "why": why})

    if a["suite"]["id"] != b["suite"]["id"]:
        add("suite", a["suite"]["id"], b["suite"]["id"], "the runs come from different suites")
    for key in ("contract", "catalog"):
        old, new = a["policy"][key], b["policy"][key]
        if old is None or new is None:
            add(key, old, new, f"the {key} could not be used in at least one run")
        elif old["sha256"] != new["sha256"]:
            add(
                key,
                old,
                new,
                f"the {key} changed, so findings may differ because of the "
                "policy rather than the candidate plans",
            )
    old_eval = a["engine"]["evaluator_sha256"]
    new_eval = b["engine"]["evaluator_sha256"]
    if old_eval is None or new_eval is None:
        add("evaluator", old_eval, new_eval, "the evaluator build of at least one run is unknown")
    elif old_eval != new_eval:
        add(
            "evaluator",
            old_eval,
            new_eval,
            "the evaluator build changed, so findings may differ because of the checker "
            "rather than the candidate plans",
        )
    if a["rules"] != b["rules"]:
        ids = sorted(
            r for r in set(a["rules"]) | set(b["rules"]) if a["rules"].get(r) != b["rules"].get(r)
        )
        add(
            "rules",
            {r: a["rules"].get(r) for r in ids},
            {r: b["rules"].get(r) for r in ids},
            "rule versions changed",
        )
    if a["report_schema"] != b["report_schema"]:
        add("report_schema", a["report_schema"], b["report_schema"], "report schemas differ")
    elif a["report_schema"] != REPORT_SCHEMA:
        add(
            "report_schema",
            a["report_schema"],
            b["report_schema"],
            f"report schema {a['report_schema']!r} is not supported; this engine reads "
            f"{REPORT_SCHEMA!r}, and two runs agreeing on an unknown schema does not make it one",
        )
    return changes


def _side(f: RunFile) -> dict[str, Any]:
    run = f.run
    return {
        "file": f.path,
        "file_sha256": f.sha256,
        "suite": run["suite"],
        "status": run["status"],
        "summary": run["summary"],
        "labels": run["labels"],
        "evaluator_sha256": run["engine"]["evaluator_sha256"],
        "engine_version": run["engine"]["version"],
    }


def compare_runs(baseline: RunFile, candidate: RunFile) -> dict[str, Any]:
    a, b = baseline.run, candidate.run
    before = {_key(c): c for c in a["cases"]}
    after = {_key(c): c for c in b["cases"]}

    def names(keys: Any) -> list[dict[str, Any]]:
        return [{"case_id": k[0], "sample": k[1]} for k in keys]

    findings: dict[str, list[dict[str, Any]]] = {
        "new": [],
        "resolved": [],
        "unchanged": [],
        "changed_evidence": [],
        "ambiguous": [],
    }
    removed = [k for k in before if k not in after]
    added = [k for k in after if k not in before]
    not_comparable: list[dict[str, Any]] = []
    compared = 0
    plans = {"changed": 0, "unchanged": 0, "unknown": 0}

    for key, old in before.items():
        new = after.get(key)
        if new is None:
            continue
        old_sha, new_sha = old["plan"]["sha256"], new["plan"]["sha256"]
        if old_sha is None or new_sha is None:
            plans["unknown"] += 1
        else:
            plans["unchanged" if old_sha == new_sha else "changed"] += 1
        if "error" in (old["verdict"], new["verdict"]):
            not_comparable.append(
                {
                    "case_id": key[0],
                    "sample": key[1],
                    "baseline_verdict": old["verdict"],
                    "candidate_verdict": new["verdict"],
                }
            )
            continue
        compared += 1
        _pair(new, old["findings"], new["findings"], findings)

    for key in added:
        case = after[key]
        if case["verdict"] == "error":
            not_comparable.append(
                {
                    "case_id": key[0],
                    "sample": key[1],
                    "baseline_verdict": None,
                    "candidate_verdict": "error",
                }
            )
        else:
            findings["new"] += [_entry(case, f) for f in case["findings"]]

    context = _context_changes(a, b)
    incomplete: list[str] = []
    if removed:
        incomplete.append(
            f"{len(removed)} case(s) in the baseline are missing from the "
            "candidate; a dropped case is not a resolved case"
        )
    if not_comparable:
        incomplete.append(
            f"{len(not_comparable)} case(s) could not be evaluated in at least one run"
        )
    if findings["ambiguous"]:
        incomplete.append(
            f"{len(findings['ambiguous'])} group(s) of findings could not be matched one to one"
        )
    for name, run in (("baseline", a), ("candidate", b)):
        if run["suite_errors"]:
            incomplete.append(f"the {name} run has suite errors")

    if context:
        status = "not_comparable"
    elif incomplete:
        status = "incomplete_comparison"
    elif findings["new"]:
        status = "new_violations"
    else:
        status = "no_new_violations"

    still_rejected = [
        {"case_id": c["case_id"], "sample": c["sample"]}
        for c in b["cases"]
        if c["verdict"] == "reject"
    ]
    replay = bool(before) and plans["changed"] == 0 and plans["unknown"] == 0 and not added
    notes = [NOT_AN_APPROVAL]
    if replay:
        notes.insert(
            0,
            "Every candidate plan is identical to its baseline plan, so this is a replay of "
            "the same content. It says nothing about a new planner, model or prompt, whatever "
            "the labels say.",
        )
    if a["suite"]["sha256"] != b["suite"]["sha256"]:
        notes.append("The suite file changed between the runs.")
    if a["engine"]["version"] != b["engine"]["version"]:
        notes.append("The engine version string changed between the runs.")

    return {
        "diff_schema": DIFF_SCHEMA,
        "engine": {"name": "planlens-agent", "version": __version__},
        "status": status,
        "exit_code": EXIT[status],
        "no_new_violations": status == "no_new_violations",
        "approval": False,
        "candidate_status": b["status"],
        "candidate_passes": b["status"] == "passed",
        "replay": replay,
        "baseline": _side(baseline),
        "candidate": _side(candidate),
        "context_changes": context,
        "incomplete_reasons": incomplete,
        "plans": plans,
        "cases": {
            "compared": compared,
            "added": names(added),
            "removed": names(removed),
            "not_comparable": not_comparable,
            "still_rejected": still_rejected,
        },
        "findings": findings,
        "summary": {name: len(items) for name, items in findings.items()},
        "notes": notes,
    }


def diff_to_json(diff: dict[str, Any]) -> str:
    return json.dumps(diff, indent=2, ensure_ascii=True) + "\n"


_HEADLINE = {
    "no_new_violations": "NO NEW VIOLATIONS",
    "new_violations": "NEW VIOLATIONS",
    "incomplete_comparison": "INCOMPLETE COMPARISON",
    "not_comparable": "NOT COMPARABLE",
}


def _labels(side: dict[str, Any]) -> str:
    if not side["labels"]:
        return ""
    shown = ", ".join(f"{safe(k)}={safe(v)}" for k, v in sorted(side["labels"].items()))
    return f"  [{shown}]"


def _at(entry: dict[str, Any]) -> str:
    label = safe(case_label(entry["case_id"], entry["sample"]))
    text = f"{label}: {safe(entry['rule_id'])}"
    if entry["contract_rule"]:
        text += f" ({safe(entry['contract_rule'])})"
    if entry["step_id"]:
        text += f" at {safe(entry['step_id'])}"
    return text


def _case_name(c: dict[str, Any]) -> str:
    return safe(case_label(c["case_id"], c["sample"]))


def render_diff(d: dict[str, Any]) -> str:
    s, cases = d["summary"], d["cases"]
    lines = [f"planlens-agent {d['engine']['version']} - suite diff"]
    for name in ("baseline", "candidate"):
        side = d[name]
        lines.append(
            f"  {name:9} {safe(side['file'])}  sha256:{side['file_sha256'][:12]}  "
            f"{side['status']}{_labels(side)}"
        )
    if d["baseline"]["labels"] or d["candidate"]["labels"]:
        lines.append("  labels are caller-supplied and not verified")
    lines.append("")
    head = f"RESULT: {_HEADLINE[d['status']]} (exit {d['exit_code']})"
    if d["replay"]:
        head += " - REPLAY of identical plans"
    lines.append(head)
    lines.append(
        f"  {s['new']} new, {s['resolved']} resolved, {s['unchanged']} unchanged, "
        f"{s['changed_evidence']} changed evidence, {s['ambiguous']} ambiguous"
    )
    lines.append(
        f"  {cases['compared']} cases compared, {len(cases['added'])} added, "
        f"{len(cases['removed'])} removed, {len(cases['not_comparable'])} not comparable"
    )
    lines.append(f"  plans: {d['plans']['changed']} changed, {d['plans']['unchanged']} identical")
    rejected = len(cases["still_rejected"])
    lines.append(
        f"  candidate run: {d['candidate_status']}"
        + (f", {rejected} case(s) rejected" if rejected else "")
    )

    if d["context_changes"]:
        lines += [
            "",
            "Not like for like. Do not attribute these differences to the candidate plans:",
        ]
        lines += [f"  {safe(c['kind'])}: {safe(c['why'])}" for c in d["context_changes"]]
    if d["incomplete_reasons"]:
        lines += ["", "Incomplete:"]
        lines += [f"  {safe(r)}" for r in d["incomplete_reasons"]]

    def section(title: str, rows: list[str]) -> None:
        if rows:
            lines.extend(["", title, *rows])

    f = d["findings"]
    section("New findings", [f"  + {_at(e)}\n      {safe(e['message'])}" for e in f["new"]])
    section("Resolved findings", [f"  - {_at(e)}" for e in f["resolved"]])
    section(
        "Changed evidence",
        [
            f"  ~ {_at(e['candidate'])}\n      was: {safe(e['baseline']['message'])}\n"
            f"      now: {safe(e['candidate']['message'])}"
            for e in f["changed_evidence"]
        ],
    )
    section(
        "Unchanged findings",
        [
            f"  = {_at(e)}"
            + (
                f"  (moved steps[{e['baseline_position']}] -> steps[{e['position']}])"
                if e["moved"]
                else ""
            )
            for e in f["unchanged"]
        ],
    )
    section(
        "Ambiguous findings (not matched)",
        [
            f"  ? {_case_name(e)}: {safe(e['rule_id'])}, {len(e['baseline'])} in the baseline, "
            f"{len(e['candidate'])} in the candidate"
            for e in f["ambiguous"]
        ],
    )
    section("Added cases", [f"  {_case_name(c)}" for c in cases["added"]])
    section(
        "Removed cases (not counted as resolved)", [f"  {_case_name(c)}" for c in cases["removed"]]
    )
    section(
        "Cases not comparable",
        [
            f"  {_case_name(c)}: baseline {c['baseline_verdict']}, "
            f"candidate {c['candidate_verdict']}"
            for c in cases["not_comparable"]
        ],
    )
    lines.append("")
    lines += [safe(n) for n in d["notes"]]
    return "\n".join(lines) + "\n"
