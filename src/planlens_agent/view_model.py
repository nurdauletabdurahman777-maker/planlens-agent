"""An explicit view of one recorded check, for the inspector.

The JSON report holds the findings but not the whole plan or policy, so the
inspector needs more than the report. This module builds that view from the
:class:`~planlens_agent.checker.CheckResult` itself - its diagnostics, its
read-only plan snapshot, and the validated policy it was checked under.

Two rules keep it honest:

* **No second verifier.** Nothing here decides whether anything passes. Every
  judgement comes from the result's diagnostics. The view only looks facts up:
  which step binds which reference, which steps perform which action.
* **No facts from wording.** What a finding points at comes from its structured
  locations and contract-rule id, never from parsing its message.

When the plan was not evaluated - invalid input or invalid policy - there is no
plan view at all, rather than a graph assembled from a document the checker
refused.
"""

from __future__ import annotations

import re
import string
from collections import Counter
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ._version import __version__

if TYPE_CHECKING:
    from .checker import CheckResult

VIEW_SCHEMA = "planlens-agent/inspector-view/v1"
_STEP_PATH = re.compile(r"^/steps/(\d+)(?:/inputs/([^/]+))?")
_RULE_PATH = re.compile(r"^/rules/(\d+)")
_REQUIRED_PATH = re.compile(r"^/required_actions/(\d+)")


def group_label(n: int) -> str:
    """The n-th group label: A..Z, AA..ZZ, AAA.. (bijective base 26, no upper limit)."""
    label = ""
    n += 1
    while n:
        n, digit = divmod(n - 1, 26)
        label = string.ascii_uppercase[digit] + label
    return label


def build_view(result: CheckResult) -> dict[str, Any]:
    from .checker import NOT_CHECKED, SCOPE

    pol = result.policy
    catalog, contract = pol.catalog, pol.contract
    diagnostics = [dict(d.to_dict(), id=f"f{i + 1}") for i, d in enumerate(result.diagnostics)]

    view: dict[str, Any] = {
        "view_schema": VIEW_SCHEMA,
        "engine": {"name": "planlens-agent", "version": __version__},
        "verdict": result.verdict,
        "exit_code": result.exit_code,
        "error_kind": result.error_kind,
        "passes_required_checks": result.passed,
        "plan_evaluated": result.plan_evaluated,
        "scope": SCOPE,
        "not_checked": list(NOT_CHECKED),
        "identity": {
            "plan": {
                "origin": result.plan_origin,
                "sha256": result.plan_digest,
                "steps": result.step_count,
            },
            "contract": (
                {
                    "origin": contract.origin,
                    "id": contract.id,
                    "version": contract.version,
                    "sha256": contract.digest,
                }
                if contract
                else None
            ),
            "catalog": (
                {
                    "origin": catalog.origin,
                    "id": catalog.id,
                    "version": catalog.version,
                    "sha256": catalog.digest,
                }
                if catalog
                else None
            ),
        },
        "unusable_documents": {
            "contract": None if contract else pol.contract_origin,
            "catalog": None if catalog else pol.catalog_origin,
        },
        "diagnostics": diagnostics,
        "plan": None,
        "required_actions": [],
        "prerequisites": [],
        "artifacts": {"producers": [], "unresolved": []},
        "same_artifact": [],
    }

    snapshot = result.plan_snapshot
    if not result.plan_evaluated or snapshot is None or catalog is None or contract is None:
        for d in diagnostics:
            d["focus"] = _focus(d, None, contract, [])
        return view

    raw_steps: list[Mapping[str, Any]] = list(snapshot["steps"])
    steps = []
    for i, s in enumerate(raw_steps):
        inputs = []
        for name, ref in sorted(dict(s.get("inputs", {})).items()):
            producer_id, output = ref.split(".", 1)
            inputs.append({"name": name, "ref": ref, "producer_id": producer_id, "output": output})
        steps.append(
            {
                "index": i,
                "id": s["id"],
                "action": s["action"],
                "action_known": s["action"] in catalog.actions,
                "description": s.get("description", ""),
                "inputs": inputs,
            }
        )
    index_of = {s["id"]: s["index"] for s in steps}
    for s in steps:
        for inp in s["inputs"]:
            inp["producer_index"] = index_of.get(inp["producer_id"])

    view["plan"] = {
        "title": snapshot.get("title"),
        "description": snapshot.get("description"),
        "steps": steps,
    }

    present: dict[str, list[int]] = {}
    for s in steps:
        present.setdefault(s["action"], []).append(s["index"])
    view["required_actions"] = [
        {
            "action": a,
            "index": i,
            "step_indices": present.get(a, []),
            "present": a in present,
            "contract_path": f"/required_actions/{i}",
        }
        for i, a in enumerate(contract.required_actions)
    ]

    view["prerequisites"] = [
        {
            "rule_id": p.id,
            "index": p.index,
            "action": p.action,
            "requires_prior": p.requires_prior,
            "because": p.because,
            "action_steps": present.get(p.action, []),
            "prior_steps": present.get(p.requires_prior, []),
            "contract_path": f"/rules/{p.index}",
        }
        for p in contract.prerequisites
    ]

    producers: dict[str, dict[str, Any]] = {}
    for s in steps:
        if not s["action_known"]:
            continue
        for output, typ in sorted(catalog.actions[s["action"]].outputs.items()):
            key = f"{s['id']}.{output}"
            producers[key] = {
                "key": key,
                "producer_id": s["id"],
                "producer_index": s["index"],
                "output": output,
                "type": typ,
                "consumers": [],
            }
    unresolved: dict[str, dict[str, Any]] = {}
    for s in steps:
        for inp in s["inputs"]:
            consumer = {"step_index": s["index"], "step_id": s["id"], "input": inp["name"]}
            target = producers.get(inp["ref"])
            if target is not None:
                target["consumers"].append(consumer)
            else:
                unresolved.setdefault(inp["ref"], {"ref": inp["ref"], "consumers": []})[
                    "consumers"
                ].append(consumer)
    view["artifacts"] = {
        "producers": list(producers.values()),
        "unresolved": list(unresolved.values()),
    }

    groups_out = []
    # One label per reference across the whole report: the step table and the diagram
    # show labels from every rule together, so a letter must mean one thing throughout.
    label: dict[str, str] = {}
    for r in contract.same_artifact:
        bindings = []
        for s in steps:
            for action, name in r.inputs:
                if s["action"] != action:
                    continue
                ref = next((x["ref"] for x in s["inputs"] if x["name"] == name), None)
                bindings.append(
                    {
                        "step_index": s["index"],
                        "step_id": s["id"],
                        "action": action,
                        "input": name,
                        "ref": ref,
                    }
                )
        refs = list(dict.fromkeys(b["ref"] for b in bindings if b["ref"] is not None))
        for ref in refs:
            label.setdefault(ref, group_label(len(label)))
        counts = Counter(b["ref"] for b in bindings)
        for b in bindings:
            b["group"] = label.get(b["ref"]) if b["ref"] is not None else None
        groups_out.append(
            {
                "rule_id": r.id,
                "index": r.index,
                "because": r.because,
                "members": [f"{a}.{n}" for a, n in r.inputs],
                "bindings": bindings,
                "groups": [
                    {
                        "label": label[ref],
                        "ref": ref,
                        "bindings": counts[ref],
                    }
                    for ref in refs
                ],
                "contract_path": f"/rules/{r.index}",
            }
        )
    view["same_artifact"] = groups_out

    for d in diagnostics:
        d["focus"] = _focus(d, steps, contract, groups_out)
    for s in steps:
        s["finding_ids"] = [d["id"] for d in diagnostics if s["index"] in d["focus"]["steps"]]
    for p in view["prerequisites"]:
        p["finding_ids"] = [
            d["id"] for d in diagnostics if d["focus"]["prerequisite_rule"] == p["rule_id"]
        ]
    for g in groups_out:
        g["finding_ids"] = [
            d["id"] for d in diagnostics if d["focus"]["same_artifact_rule"] == g["rule_id"]
        ]
    for ra in view["required_actions"]:
        ra["finding_ids"] = [
            d["id"] for d in diagnostics if d["focus"]["required_action"] == ra["action"]
        ]
    return view


def _locations(d: dict[str, Any]) -> list[dict[str, Any]]:
    locs = [d["location"]] if d["location"] else []
    return locs + list(d["related_locations"])


def _focus(
    d: dict[str, Any],
    steps: list[dict[str, Any]] | None,
    contract: Any,
    groups: list[dict[str, Any]],
) -> dict[str, Any]:
    """What a finding points at, from its structured locations only."""
    focus: dict[str, Any] = {
        "steps": [],
        "inputs": [],
        "artifact_keys": [],
        "producer_steps": [],
        "prerequisite_rule": None,
        "same_artifact_rule": None,
        "required_action": None,
        "contract_paths": [],
    }
    for loc in _locations(d):
        if loc["document"] == "plan":
            m = _STEP_PATH.match(loc["path"] or "")
            if m:
                i = int(m.group(1))
                if i not in focus["steps"]:
                    focus["steps"].append(i)
                if m.group(2):
                    focus["inputs"].append([i, m.group(2)])
                    if steps is not None and i < len(steps):
                        ref = next(
                            (x["ref"] for x in steps[i]["inputs"] if x["name"] == m.group(2)), None
                        )
                        if ref and ref not in focus["artifact_keys"]:
                            focus["artifact_keys"].append(ref)
        elif loc["document"] == "contract":
            focus["contract_paths"].append(loc["path"])
            m = _REQUIRED_PATH.match(loc["path"] or "")
            if m and contract is not None:
                k = int(m.group(1))
                if k < len(contract.required_actions):
                    focus["required_action"] = contract.required_actions[k]
    rule = d.get("contract_rule")
    if rule and contract is not None:
        if any(p.id == rule for p in contract.prerequisites):
            focus["prerequisite_rule"] = rule
        if any(r.id == rule for r in contract.same_artifact):
            focus["same_artifact_rule"] = rule
    if steps is not None:
        # Producers of the references the finding points at, looked up in the plan so the
        # developer sees where each compared artifact comes from. Not part of the finding.
        index_of = {st["id"]: st["index"] for st in steps}
        for key in focus["artifact_keys"]:
            j = index_of.get(key.split(".", 1)[0])
            if j is not None and j not in focus["steps"] and j not in focus["producer_steps"]:
                focus["producer_steps"].append(j)
    focus["steps"].sort()
    focus["producer_steps"].sort()
    return focus
