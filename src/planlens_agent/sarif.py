"""SARIF 2.1.0 export of one recorded check.

Built from the result's diagnostics and the rule registry - the same data as the
JSON report. The mapping, chosen so that a consumer can tell a completed
analysis that rejects a plan from one that could not evaluate the required
checks:

==========================  ==========================================  ====================
finding category            SARIF                                       invocation
==========================  ==========================================  ====================
``violation``               result, ``kind: fail``, ``level: error``    executionSuccessful
``invalid_plan``            result, ``kind: fail``, ``level: error``    false
``unsupported``             result, ``kind: open``, ``level: none``     false
``invalid_contract``,       ``toolConfigurationNotifications``          false
``invalid_catalog``
==========================  ==========================================  ====================

``kind: open`` is SARIF's "could not determine whether this passes or fails";
SARIF requires ``level`` to be ``none`` for any kind other than ``fail``.

Locations:

* every finding keeps its logical location (``plan:/steps/3/inputs/artifact``);
* a physical location is added only when the document came from a real file;
  it is relative to ``SRCROOT`` when the file is inside the source root, and an
  absolute ``file:`` URI otherwise. The absolute path of ``SRCROOT`` itself is
  not written into the log;
* a ``region`` (line, column) is added only when a parser reported one. Plan
  positions are never turned into line numbers;
* documents passed to the SDK as mappings have no physical location.

The engine version is a PEP 440 string (``0.1.0.dev0``), which is not SemVer, so
it goes in ``tool.driver.version`` and ``semanticVersion`` is omitted.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from ._version import __version__
from .diagnostics import RULES, Diagnostic, Location

if TYPE_CHECKING:
    from .checker import CheckResult

SARIF_SCHEMA_URI = (
    "https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json"
)
SRCROOT = "SRCROOT"
_CONFIG = {"invalid_contract", "invalid_catalog"}


class _Locator:
    """Turns a document's load-time source path into an artifactLocation.

    Uses only what was recorded when the document was read. It does not look at the
    filesystem or the current directory, so a result exports the same way from anywhere.
    """

    def __init__(self, sources: dict[str, str | None], source_root: Path | None) -> None:
        self.root = source_root.resolve() if source_root is not None else None
        self.artifacts: list[dict[str, Any]] = []
        self.index: dict[str, int] = {}
        self.where: dict[str, dict[str, Any] | None] = {}
        for doc, source in sources.items():
            self.where[doc] = self._artifact(doc, source)

    def _artifact(self, doc: str, source: str | None) -> dict[str, Any] | None:
        if source is None:
            return None  # a mapping, or a file that did not exist: no physical location
        resolved = Path(source)
        loc: dict[str, Any]
        if self.root is not None and resolved.is_relative_to(self.root):
            rel = resolved.relative_to(self.root).as_posix()
            loc = {"uri": quote(os.fsencode(rel), safe="/"), "uriBaseId": SRCROOT}
        else:
            loc = {"uri": resolved.as_uri()}
        key = json.dumps(loc, sort_keys=True)
        if key not in self.index:
            self.index[key] = len(self.artifacts)
            self.artifacts.append(
                {
                    "location": dict(loc),
                    "roles": ["analysisTarget"]
                    if doc == "plan"
                    else ["userSpecifiedConfiguration"],
                    "properties": {"planlens_document": doc},
                }
            )
        return dict(loc, index=self.index[key])


def _location(
    loc: Location, locator: _Locator, message: str | None = None, loc_id: int | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if loc_id is not None:
        out["id"] = loc_id
    artifact = locator.where.get(loc.document)
    if artifact is not None:
        physical: dict[str, Any] = {"artifactLocation": artifact}
        if loc.source_span is not None:
            physical["region"] = {
                "startLine": loc.source_span.line,
                "startColumn": loc.source_span.column,
            }
        out["physicalLocation"] = physical
    logical: dict[str, Any] = {
        "fullyQualifiedName": f"{loc.document}:{loc.path or '/'}",
        "kind": "element",
        "properties": {"document": loc.document, "path": loc.path or "/"},
    }
    if loc.step_id is not None:
        logical["name"] = loc.step_id
    if loc.step_index is not None:
        logical["properties"]["step_index"] = loc.step_index
    out["logicalLocations"] = [logical]
    if message:
        out["message"] = {"text": message}
    return out


def _rule_descriptor(rule_id: str) -> dict[str, Any]:
    info = RULES[rule_id]
    help_lines = [info.summary]
    if info.bad_example:
        help_lines.append(f"Triggers it: {info.bad_example}")
    if info.good_example:
        help_lines.append(f"Satisfies it: {info.good_example}")
    if info.limitations:
        help_lines.append(f"Limits: {info.limitations}")
    help_lines.append(f"Offline help: planlens-agent explain {rule_id}")
    return {
        "id": rule_id,
        "name": "".join(w.capitalize() for w in rule_id.replace("/", "-").split("-")),
        "shortDescription": {"text": info.title},
        "fullDescription": {"text": info.summary},
        "help": {"text": "\n".join(help_lines)},
        "defaultConfiguration": {"level": "none" if info.category == "unsupported" else "error"},
        "properties": {"rule_version": info.version, "category": info.category},
    }


def _fingerprint(d: Diagnostic) -> str:
    loc = d.location
    basis = [
        d.rule_id,
        loc.document if loc else None,
        loc.path if loc else None,
        d.contract_rule,
        [r.path for r in d.related],
    ]
    return hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()[:32]


def to_sarif(result: CheckResult, source_root: str | Path | None = None) -> dict[str, Any]:
    """The SARIF 2.1.0 log for one check.

    ``source_root`` is the directory relative paths are expressed against
    (``SRCROOT``). Files outside it get absolute ``file:`` URIs.
    """
    from .checker import NOT_CHECKED, SCOPE

    pol = result.policy
    root = Path(source_root) if source_root is not None else None
    locator = _Locator(
        {
            "plan": result.plan_source,
            "contract": pol.contract_source,
            "catalog": pol.catalog_source,
        },
        root,
    )

    # SARIF keeps result rules and notification descriptors in separate arrays, and a
    # descriptor's index refers to its own array.
    rule_ids = sorted(r for r in RULES if RULES[r].category not in _CONFIG)
    note_ids = sorted(r for r in RULES if RULES[r].category in _CONFIG)
    rule_index = {r: i for i, r in enumerate(rule_ids)}
    note_index = {r: i for i, r in enumerate(note_ids)}
    results: list[dict[str, Any]] = []
    config_notes: list[dict[str, Any]] = []

    for d in result.diagnostics:
        category = d.category
        related = [_location(r, locator, loc_id=n + 1) for n, r in enumerate(d.related)]
        props: dict[str, Any] = {
            "category": category,
            "rule_version": d.rule.version,
            "observed": d.observed,
            "required": d.required,
        }
        if d.contract_rule:
            props["contract_rule"] = d.contract_rule
        if d.suggestion:
            props["suggestion"] = d.suggestion
        if d.evidence:
            props["evidence"] = d.evidence
        if d.location is None and d.location_absent_reason:
            props["location_absent_reason"] = d.location_absent_reason
        locations = [_location(d.location, locator)] if d.location is not None else []

        if category in _CONFIG:
            note: dict[str, Any] = {
                "descriptor": {"id": d.rule_id, "index": note_index[d.rule_id]},
                "level": "error",
                "message": {"text": d.message},
                "properties": props,
            }
            if locations:
                note["locations"] = locations
            config_notes.append(note)
            continue

        res: dict[str, Any] = {
            "ruleId": d.rule_id,
            "ruleIndex": rule_index[d.rule_id],
            "message": {"text": d.message},
            "partialFingerprints": {"planlensFinding/v1": _fingerprint(d)},
            "properties": props,
        }
        if category == "unsupported":
            res["kind"], res["level"] = "open", "none"
        else:
            res["kind"], res["level"] = "fail", "error"
        if locations:
            res["locations"] = locations
        if related:
            res["relatedLocations"] = related
        results.append(res)

    completed = result.verdict in ("pass", "reject")
    invocation: dict[str, Any] = {
        "executionSuccessful": completed,
        "exitCode": result.exit_code,
        "properties": {
            "verdict": result.verdict,
            "error_kind": result.error_kind,
            "plan_evaluated": result.plan_evaluated,
            "analysis": (
                "completed: every supported required check was evaluated"
                if completed
                else "incomplete: required checks could not be fully evaluated"
            ),
        },
    }
    if config_notes:
        invocation["toolConfigurationNotifications"] = config_notes

    run: dict[str, Any] = {
        "tool": {
            "driver": {
                "name": "planlens-agent",
                "version": __version__,
                "rules": [_rule_descriptor(r) for r in rule_ids],
                "notifications": [_rule_descriptor(r) for r in note_ids],
                "properties": {"version_scheme": "PEP 440 (not SemVer)"},
            }
        },
        "invocations": [invocation],
        "columnKind": "unicodeCodePoints",
        "results": results,
        "properties": {
            "scope": SCOPE,
            "not_checked": list(NOT_CHECKED),
            "verdict": result.verdict,
            "plan": {"canonical_sha256": result.plan_digest},
            "contract": (
                {
                    "id": pol.contract.id,
                    "version": pol.contract.version,
                    "canonical_sha256": pol.contract.digest,
                }
                if pol.contract
                else None
            ),
            "catalog": (
                {
                    "id": pol.catalog.id,
                    "version": pol.catalog.version,
                    "canonical_sha256": pol.catalog.digest,
                }
                if pol.catalog
                else None
            ),
            "digest_note": "canonical_sha256 is over the canonical JSON encoding of the "
            "parsed document, not over the file bytes",
            "location_note": "file locations record where each document was read when it was "
            "checked; they do not show that the file is unchanged since then",
        },
    }
    if locator.artifacts:
        run["artifacts"] = locator.artifacts
    if locator.root is not None and any("uriBaseId" in (a or {}) for a in locator.where.values()):
        # Deliberately no absolute "uri": a SARIF log is often uploaded or shared, and the
        # absolute source root would disclose local paths. Consumers map SRCROOT to their
        # checkout root, as code-scanning services do.
        run["originalUriBaseIds"] = {
            SRCROOT: {
                "description": {
                    "text": "The directory given with --source-root (default: the current "
                    "directory). Relative URIs are resolved against it; its absolute "
                    "path is intentionally not recorded."
                }
            }
        }
    return {"$schema": SARIF_SCHEMA_URI, "version": "2.1.0", "runs": [run]}


def to_sarif_json(result: CheckResult, source_root: str | Path | None = None) -> str:
    return json.dumps(to_sarif(result, source_root), indent=2, ensure_ascii=True) + "\n"
