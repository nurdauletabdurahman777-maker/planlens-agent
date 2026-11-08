"""Run a set of candidate plans and save the result.

The question this answers is: after changing a planner, model or prompt, which candidate
plans violate the workflow requirements? The developer supplies the candidates; nothing
here calls a model or infers a requirement. Each case goes through :func:`check`, so
there is no second evaluator.

A saved run is a **record**. It holds what was checked and what was found, and it is not
an approval: nothing in it can be dispatched. Execution still goes through
``CheckResult.approve()`` and ``ReferenceRunner``.

This differs from ``regress``, which tests recorded expectations about the checker.

Run files are read back as untrusted input. :func:`load_run` validates their structure,
applies size limits, re-verifies each plan's digest against its recorded content, and
recomputes the run's status from its cases. That catches corruption and careless edits.
It does not prove who produced a run: run files are not signed.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from ._version import __version__
from .checker import REPORT_SCHEMA, check
from .diagnostics import RULES
from .loading import canonical_bytes, digest, load, parse_json_document
from .policy import validate_policy
from .report import safe

SUITE_SCHEMA = "planlens-agent/suite/v1"
RUN_SCHEMA = "planlens-agent/run/v1"

MAX_CASES = 2000
MAX_RUN_BYTES = 64_000_000
MAX_LABELS = 32
MAX_LABEL_VALUE = 200
MAX_RUN_DEPTH = 64

CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SUITE_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
LABEL_KEY = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")

#: The modules that decide verdicts and findings. Their digest tells apart development
#: builds that share a version string. Any edit to them changes it, including a harmless
#: one: the digest shows that the evaluator changed, not whether its behaviour did.
EVALUATOR_MODULES = (
    "_version.py",
    "checker.py",
    "diagnostics.py",
    "loading.py",
    "plan.py",
    "policy.py",
)

NOT_AN_APPROVAL = (
    "A saved run is a record of a check. It is not an approval and cannot be "
    "dispatched; execution goes through check().approve() and ReferenceRunner."
)
LABELS_NOTE = (
    "Labels are supplied by the caller and are not verified. They do not establish "
    "which model, prompt or provider produced a plan."
)

STATUS_EXIT = {"passed": 0, "rejected": 1, "incomplete": 2}
VERDICT_EXIT = {"pass": 0, "reject": 1, "error": 2}
SUITE_ERROR_CODES = ("invalid-case", "duplicate-case")


class SuiteFileError(Exception):
    """The suite file itself cannot be used, so nothing was run."""


class RunFileError(Exception):
    """A saved run is unreadable, invalid or internally inconsistent."""


def evaluator_digest() -> str | None:
    """SHA-256 over the evaluating modules, or None if their source is not available."""
    package = resources.files("planlens_agent")
    h = hashlib.sha256()
    try:
        for name in EVALUATOR_MODULES:
            h.update(name.encode() + b"\0" + package.joinpath(name).read_bytes() + b"\0")
    except OSError:
        return None
    return h.hexdigest()


@dataclass(frozen=True)
class SuiteCase:
    case_id: str
    sample: str | None
    plan: str


@dataclass(frozen=True)
class Suite:
    id: str
    sha256: str
    base: Path
    contract: str
    catalog: str
    plans_dir: str
    labels: Mapping[str, str]
    cases: tuple[SuiteCase, ...]
    errors: tuple[dict[str, Any], ...] = field(default=())
    declared_plans: tuple[str, ...] = field(default=())  # every plan path listed, even if invalid


def case_label(case_id: str, sample: str | None) -> str:
    return f"{case_id}#{sample}" if sample is not None else case_id


def _problem(code: str, message: str, path: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "path": path, **extra}


def load_suite(path: str | Path) -> Suite:
    """Read a suite file. Bad entries are recorded; an unusable file raises SuiteFileError."""
    path = Path(path)
    loaded = load(path, "suite")
    if loaded.data is None:
        raise SuiteFileError("; ".join(d.message for d in loaded.diagnostics))
    data = loaded.data
    if data.get("schema") != SUITE_SCHEMA:
        raise SuiteFileError(f"'schema' must be {SUITE_SCHEMA!r}")
    allowed = {"schema", "id", "description", "contract", "catalog", "plans_dir", "labels", "cases"}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise SuiteFileError(f"unknown fields {unknown}; unknown fields are not ignored")
    suite_id = data.get("id")
    if not isinstance(suite_id, str) or not SUITE_ID.fullmatch(suite_id):
        raise SuiteFileError(f"'id' must match {SUITE_ID.pattern}")
    for name in ("contract", "catalog"):
        if not isinstance(data.get(name), str):
            raise SuiteFileError(f"{name!r} must be a path relative to the suite file")
    if not isinstance(data.get("description", ""), str):
        raise SuiteFileError("'description' must be a string")
    plans_dir = data.get("plans_dir", ".")
    if not isinstance(plans_dir, str):
        raise SuiteFileError("'plans_dir' must be a path relative to the suite file")
    try:
        labels = validate_labels(data.get("labels", {}))
    except ValueError as exc:
        raise SuiteFileError(str(exc)) from None
    raw_cases = data.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise SuiteFileError("'cases' must be a non-empty list")
    if len(raw_cases) > MAX_CASES:
        raise SuiteFileError(f"{len(raw_cases)} cases; the limit is {MAX_CASES}")

    errors: list[dict[str, Any]] = []
    parsed: list[tuple[int, SuiteCase]] = []
    for i, raw in enumerate(raw_cases):
        where = f"/cases/{i}"
        if not isinstance(raw, dict):
            errors.append(_problem("invalid-case", "each case must be a table", where))
            continue
        extra = sorted(set(raw) - {"id", "sample", "plan", "note"})
        cid, sample, plan = raw.get("id"), raw.get("sample"), raw.get("plan")
        if extra:
            errors.append(_problem("invalid-case", f"unknown case fields {extra}", where))
        elif not isinstance(cid, str) or not CASE_ID.fullmatch(cid):
            errors.append(_problem("invalid-case", f"'id' must match {CASE_ID.pattern}", where))
        elif sample is not None and (not isinstance(sample, str) or not CASE_ID.fullmatch(sample)):
            errors.append(_problem("invalid-case", f"'sample' must match {CASE_ID.pattern}", where))
        elif not isinstance(plan, str) or not plan:
            errors.append(_problem("invalid-case", "'plan' must be a path", where))
        elif not isinstance(raw.get("note", ""), str):
            errors.append(_problem("invalid-case", "'note' must be a string", where))
        else:
            parsed.append((i, SuiteCase(cid, sample, plan)))

    # A repeated id leaves no way to tell which entry is the case, so neither is evaluated.
    counts = Counter((c.case_id, c.sample) for _, c in parsed)
    cases: list[SuiteCase] = []
    reported: set[tuple[str, str | None]] = set()
    for i, c in parsed:
        key = (c.case_id, c.sample)
        if counts[key] == 1:
            cases.append(c)
        elif key not in reported:
            reported.add(key)
            errors.append(
                _problem(
                    "duplicate-case",
                    f"case {case_label(*key)!r} appears {counts[key]} times, so none of its "
                    "entries was evaluated; give each its own id or sample",
                    f"/cases/{i}",
                    case_id=c.case_id,
                    sample=c.sample,
                )
            )
    return Suite(
        suite_id,
        digest(data),
        path.parent,
        data["contract"],
        data["catalog"],
        plans_dir,
        labels,
        tuple(cases),
        tuple(errors),
        tuple(
            r["plan"] for r in raw_cases if isinstance(r, dict) and isinstance(r.get("plan"), str)
        ),
    )


def validate_labels(labels: Any) -> dict[str, str]:
    if not isinstance(labels, Mapping):
        raise ValueError("labels must be a table of strings")
    if len(labels) > MAX_LABELS:
        raise ValueError(f"{len(labels)} labels; the limit is {MAX_LABELS}")
    out: dict[str, str] = {}
    for key, value in labels.items():
        if not isinstance(key, str) or not LABEL_KEY.fullmatch(key):
            raise ValueError(f"label name {key!r} must match {LABEL_KEY.pattern}")
        if not isinstance(value, str) or len(value) > MAX_LABEL_VALUE:
            raise ValueError(
                f"label {key!r} must be a string of at most {MAX_LABEL_VALUE} characters"
            )
        out[key] = value
    return out


def _plans_base(suite: Suite, plans_dir: str | Path | None) -> Path:
    return Path(plans_dir) if plans_dir is not None else suite.base / suite.plans_dir


def suite_inputs(suite: Suite, plans_dir: str | Path | None = None) -> list[tuple[str, Path]]:
    """The policy and candidate files a run of ``suite`` reads, labelled by what they are."""
    base = _plans_base(suite, plans_dir)
    return [
        ("contract", suite.base / suite.contract),
        ("catalog", suite.base / suite.catalog),
        *(("candidate plan", base / plan) for plan in suite.declared_plans),
    ]


def run_suite(
    suite: Suite,
    plans_dir: str | Path | None = None,
    labels: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Check every case and return the run document. Input problems stay in the run."""
    policy = validate_policy(suite.base / suite.contract, suite.base / suite.catalog)
    base = _plans_base(suite, plans_dir)
    merged = validate_labels({**suite.labels, **(labels or {})})

    cases = []
    for c in suite.cases:
        result = check(base / c.plan, policy=policy)
        snapshot = result.plan_snapshot
        cases.append(
            {
                "case_id": c.case_id,
                "sample": c.sample,
                "plan": {
                    "path": c.plan,
                    "sha256": result.plan_digest,
                    "steps": result.step_count,
                    "canonical": (
                        canonical_bytes(snapshot).decode("utf-8") if snapshot is not None else None
                    ),
                },
                "verdict": result.verdict,
                "exit_code": result.exit_code,
                "error_kind": result.error_kind,
                "plan_evaluated": result.plan_evaluated,
                "findings": [d.to_dict() for d in result.diagnostics],
            }
        )

    status, summary = summarise(cases, list(suite.errors))
    con, cat = policy.contract, policy.catalog
    return {
        "run_schema": RUN_SCHEMA,
        "report_schema": REPORT_SCHEMA,
        "engine": {
            "name": "planlens-agent",
            "version": __version__,
            "evaluator_sha256": evaluator_digest(),
        },
        "runtime": {"python": platform.python_version()},
        "rules": {r: RULES[r].version for r in sorted(RULES)},
        "suite": {"id": suite.id, "sha256": suite.sha256},
        "plans_dir": str(plans_dir) if plans_dir is not None else suite.plans_dir,
        "policy": {
            "contract": (
                {"id": con.id, "version": con.version, "sha256": con.digest} if con else None
            ),
            "catalog": (
                {"id": cat.id, "version": cat.version, "sha256": cat.digest} if cat else None
            ),
        },
        "labels": merged,
        "labels_note": LABELS_NOTE,
        "status": status,
        "exit_code": STATUS_EXIT[status],
        "summary": summary,
        "suite_errors": list(suite.errors),
        "cases": cases,
        "note": NOT_AN_APPROVAL,
    }


def summarise(
    cases: list[dict[str, Any]], errors: list[dict[str, Any]]
) -> tuple[str, dict[str, int]]:
    verdicts = Counter(c["verdict"] for c in cases)
    summary = {
        "cases": len(cases),
        "passed": verdicts["pass"],
        "rejected": verdicts["reject"],
        "incomplete": verdicts["error"],
        "suite_errors": len(errors),
        "findings": sum(len(c["findings"]) for c in cases),
    }
    if errors or verdicts["error"] or not cases:
        return "incomplete", summary
    return ("rejected" if verdicts["reject"] else "passed"), summary


def run_to_json(run: Mapping[str, Any]) -> str:
    return json.dumps(run, indent=2, ensure_ascii=True) + "\n"


_HEADLINE = {"passed": "PASSED", "rejected": "REJECTED", "incomplete": "INCOMPLETE"}


def render_run(run: Mapping[str, Any]) -> str:
    s = run["summary"]
    lines = [f"planlens-agent {run['engine']['version']} - suite run"]
    lines.append(f"  suite     {safe(run['suite']['id'])}  sha256:{run['suite']['sha256'][:12]}")
    for key in ("contract", "catalog"):
        doc = run["policy"][key]
        shown = (
            f"{safe(doc['id'])} {safe(doc['version'])}  sha256:{doc['sha256'][:12]}"
            if doc
            else "not usable"
        )
        lines.append(f"  {key:9} {shown}")
    lines.append(f"  plans     {safe(run['plans_dir'])}")
    evaluator = run["engine"]["evaluator_sha256"]
    lines.append(f"  evaluator sha256:{evaluator[:12] if evaluator else 'unknown'}")
    if run["labels"]:
        shown = ", ".join(f"{safe(k)}={safe(v)}" for k, v in sorted(run["labels"].items()))
        lines.append(f"  labels    {shown} (caller-supplied, not verified)")
    lines.append("")
    lines.append(
        f"RESULT: {_HEADLINE[run['status']]} (exit {run['exit_code']}) - {s['cases']} "
        f"cases: {s['passed']} passed, {s['rejected']} rejected, "
        f"{s['incomplete']} could not be evaluated"
    )
    lines.append("")
    width = max([len(case_label(c["case_id"], c["sample"])) for c in run["cases"]], default=0)
    for c in run["cases"]:
        label = case_label(c["case_id"], c["sample"])
        lines.append(f"  {c['verdict']:<6}  {label:<{width}}  {_case_line(c)}".rstrip())
    for e in run["suite_errors"]:
        lines.append(f"  error   {safe(e['path'])}: {safe(e['message'])}")
    lines.append("")
    if run["status"] == "incomplete":
        lines.append(
            "This run is incomplete: results above are kept, but required checks "
            "were not all evaluated."
        )
    lines.append("A saved run is a record, not an approval.")
    return "\n".join(lines) + "\n"


def _case_line(c: Mapping[str, Any]) -> str:
    if not c["findings"]:
        return ""
    first = c["findings"][0]
    more = len(c["findings"]) - 1
    loc = first["location"]
    where = f" at {safe(loc['step_id'])}" if loc and loc.get("step_id") else ""
    return safe(first["rule_id"]) + where + (f" (+{more} more)" if more else "")


# ---------------------------------------------------------------------------
# Reading a saved run
# ---------------------------------------------------------------------------
RUN_FIELDS = {
    "run_schema",
    "report_schema",
    "engine",
    "runtime",
    "rules",
    "suite",
    "plans_dir",
    "policy",
    "labels",
    "labels_note",
    "status",
    "exit_code",
    "summary",
    "suite_errors",
    "cases",
    "note",
}
CASE_FIELDS = {
    "case_id",
    "sample",
    "plan",
    "verdict",
    "exit_code",
    "error_kind",
    "plan_evaluated",
    "findings",
}
PLAN_FIELDS = {"path", "sha256", "steps", "canonical"}
FINDING_FIELDS = {
    "rule_id",
    "rule_version",
    "category",
    "title",
    "message",
    "observed",
    "required",
    "location",
    "location_absent_reason",
    "related_locations",
    "contract_rule",
    "explanation",
    "suggestion",
    "evidence",
}
LOCATION_FIELDS = {"document", "path", "step_id", "step_index", "source_span"}


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise RunFileError(message)


def _fields(value: Any, expected: set[str], where: str) -> None:
    _need(isinstance(value, dict), f"{where} must be an object")
    missing, extra = sorted(expected - set(value)), sorted(set(value) - expected)
    _need(not missing, f"{where} is missing {missing}")
    _need(not extra, f"{where} has unknown fields {extra}")


def _optional_sha(value: Any, where: str) -> None:
    _need(
        value is None or (isinstance(value, str) and bool(SHA256.fullmatch(value))),
        f"{where} must be a SHA-256 hex digest or null",
    )


def _string(value: Any, where: str) -> None:
    _need(isinstance(value, str), f"{where} must be a string")


def _optional_string(value: Any, where: str) -> None:
    _need(value is None or isinstance(value, str), f"{where} must be a string or null")


def _integer(value: Any, where: str) -> None:
    _need(type(value) is int and value >= 0, f"{where} must be a non-negative integer")


def _location(loc: Any, where: str) -> None:
    _fields(loc, LOCATION_FIELDS, where)
    _need(
        isinstance(loc["document"], str) and loc["document"] in ("plan", "contract", "catalog"),
        f"{where}.document is invalid",
    )
    _string(loc["path"], f"{where}.path")
    _optional_string(loc["step_id"], f"{where}.step_id")
    if loc["step_index"] is not None:
        _integer(loc["step_index"], f"{where}.step_index")
    span = loc["source_span"]
    if span is not None:
        _fields(span, {"line", "column"}, f"{where}.source_span")
        _integer(span["line"], f"{where}.source_span.line")
        _integer(span["column"], f"{where}.source_span.column")


def _finding(f: Any, where: str, rules: dict[str, int]) -> None:
    _fields(f, FINDING_FIELDS, where)
    _need(isinstance(f["rule_id"], str) and bool(f["rule_id"]), f"{where}.rule_id is invalid")
    _integer(f["rule_version"], f"{where}.rule_version")
    _need(
        rules.get(f["rule_id"]) == f["rule_version"],
        f"{where}: rule {f['rule_id']!r} version {f['rule_version']} is not the version this "
        "run records in 'rules'",
    )
    for name in ("category", "title", "message", "explanation", "suggestion"):
        _string(f[name], f"{where}.{name}")
    _optional_string(f["contract_rule"], f"{where}.contract_rule")
    _optional_string(f["location_absent_reason"], f"{where}.location_absent_reason")
    if f["location"] is not None:
        _location(f["location"], f"{where}.location")
    _need(isinstance(f["related_locations"], list), f"{where}.related_locations must be a list")
    for i, r in enumerate(f["related_locations"]):
        _location(r, f"{where}.related_locations[{i}]")
    _need(isinstance(f["evidence"], dict), f"{where}.evidence must be an object")


def _case(c: Any, where: str, rules: dict[str, int]) -> None:
    _fields(c, CASE_FIELDS, where)
    _need(
        isinstance(c["case_id"], str) and bool(CASE_ID.fullmatch(c["case_id"])),
        f"{where}.case_id is invalid",
    )
    sample = c["sample"]
    _need(
        sample is None or (isinstance(sample, str) and bool(CASE_ID.fullmatch(sample))),
        f"{where}.sample is invalid",
    )
    verdict = c["verdict"]
    _need(
        isinstance(verdict, str) and verdict in VERDICT_EXIT,
        f"{where}.verdict is invalid; expected one of {sorted(VERDICT_EXIT)}",
    )
    _integer(c["exit_code"], f"{where}.exit_code")
    _need(
        c["exit_code"] == VERDICT_EXIT[verdict],
        f"{where}: exit_code does not match verdict {verdict!r}",
    )
    _optional_string(c["error_kind"], f"{where}.error_kind")
    _need(
        (verdict == "error") == (c["error_kind"] is not None),
        f"{where}: error_kind does not match verdict {verdict!r}",
    )
    _need(isinstance(c["plan_evaluated"], bool), f"{where}.plan_evaluated must be a boolean")
    _need(isinstance(c["findings"], list), f"{where}.findings must be a list")
    for i, f in enumerate(c["findings"]):
        _finding(f, f"{where}.findings[{i}]", rules)
    _need(
        verdict != "pass" or (not c["findings"] and c["plan_evaluated"]),
        f"{where}: a passing case cannot have findings",
    )
    _need(
        verdict == "pass" or bool(c["findings"]),
        f"{where}: a case that did not pass must record why",
    )
    _need(verdict != "reject" or c["plan_evaluated"], f"{where}: a rejected case must be evaluated")
    plan = c["plan"]
    _fields(plan, PLAN_FIELDS, f"{where}.plan")
    _optional_sha(plan["sha256"], f"{where}.plan.sha256")
    canonical = plan["canonical"]
    _string(plan["path"], f"{where}.plan.path")
    if plan["steps"] is not None:
        _integer(plan["steps"], f"{where}.plan.steps")
    _optional_string(canonical, f"{where}.plan.canonical")
    _need(
        (canonical is None) == (plan["sha256"] is None),
        f"{where}: plan digest and recorded content must both be present or both absent",
    )
    if canonical is not None:
        try:
            actual = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        except UnicodeEncodeError:
            raise RunFileError(f"{where}.plan.canonical is not Unicode text") from None
        _need(
            actual == plan["sha256"],
            f"{where}: the recorded plan content does not match its digest",
        )
    _need(
        verdict == "error" or canonical is not None,
        f"{where}: an evaluated case must record what was checked",
    )


def validate_run(doc: Any) -> None:
    """Raise RunFileError unless ``doc`` is a well-formed, self-consistent run.

    Every value is type-checked before anything relies on it. This establishes structure
    and internal consistency only. A supported ``report_schema`` is a compatibility
    question for the comparison, and nothing here shows who produced the run.
    """
    _need(isinstance(doc, dict), "a run must be a JSON object")
    version = doc.get("run_schema")
    _need(version == RUN_SCHEMA, f"'run_schema' is {version!r}; this engine reads {RUN_SCHEMA!r}")
    _fields(doc, RUN_FIELDS, "the run")
    _string(doc["report_schema"], "'report_schema'")
    _fields(doc["engine"], {"name", "version", "evaluator_sha256"}, "engine")
    _need(
        doc["engine"]["name"] == "planlens-agent",
        f"engine.name is {doc['engine']['name']!r}; this tool reads runs from 'planlens-agent'",
    )
    _optional_sha(doc["engine"]["evaluator_sha256"], "engine.evaluator_sha256")
    _string(doc["engine"]["version"], "engine.version")
    _fields(doc["runtime"], {"python"}, "runtime")
    _string(doc["runtime"]["python"], "runtime.python")
    _need(
        isinstance(doc["rules"], dict) and all(type(v) is int for v in doc["rules"].values()),
        "'rules' must map rule ids to integer versions",
    )
    _fields(doc["suite"], {"id", "sha256"}, "suite")
    _need(
        isinstance(doc["suite"]["id"], str) and bool(SUITE_ID.fullmatch(doc["suite"]["id"])),
        "suite.id is invalid",
    )
    _need(
        isinstance(doc["suite"]["sha256"], str) and bool(SHA256.fullmatch(doc["suite"]["sha256"])),
        "suite.sha256 must be a SHA-256 hex digest",
    )
    _string(doc["plans_dir"], "'plans_dir'")
    _string(doc["labels_note"], "'labels_note'")
    _string(doc["note"], "'note'")
    _fields(doc["policy"], {"contract", "catalog"}, "policy")
    for name in ("contract", "catalog"):
        part = doc["policy"][name]
        if part is not None:
            _fields(part, {"id", "version", "sha256"}, f"policy.{name}")
            _string(part["id"], f"policy.{name}.id")
            _string(part["version"], f"policy.{name}.version")
            _need(
                isinstance(part["sha256"], str) and bool(SHA256.fullmatch(part["sha256"])),
                f"policy.{name}.sha256 must be a SHA-256 hex digest",
            )
    try:
        validate_labels(doc["labels"])
    except ValueError as exc:
        raise RunFileError(str(exc)) from None
    cases = doc["cases"]
    _need(isinstance(cases, list), "'cases' must be a list")
    _need(len(cases) <= MAX_CASES, f"{len(cases)} cases; the limit is {MAX_CASES}")
    seen: set[tuple[str, str | None]] = set()
    for i, c in enumerate(cases):
        _case(c, f"cases[{i}]", doc["rules"])
        key = (c["case_id"], c["sample"])
        _need(key not in seen, f"cases[{i}] repeats case {case_label(*key)!r}")
        seen.add(key)
    errors = doc["suite_errors"]
    _need(isinstance(errors, list), "'suite_errors' must be a list")
    for i, e in enumerate(errors):
        _need(isinstance(e, dict), f"suite_errors[{i}] must be an object")
        extra = sorted(set(e) - {"code", "message", "path", "case_id", "sample"})
        _need(not extra, f"suite_errors[{i}] has unknown fields {extra}")
        _need(
            isinstance(e.get("code"), str) and e["code"] in SUITE_ERROR_CODES,
            f"suite_errors[{i}].code is invalid",
        )
        _need(
            isinstance(e.get("message"), str) and isinstance(e.get("path"), str),
            f"suite_errors[{i}] needs a string message and path",
        )
    status, summary = summarise(cases, errors)
    _need(doc["status"] == status, f"'status' says {doc['status']!r} but the cases give {status!r}")
    _integer(doc["exit_code"], "'exit_code'")
    _need(doc["exit_code"] == STATUS_EXIT[status], "'exit_code' does not match the status")
    _need(
        isinstance(doc["summary"], dict) and all(type(v) is int for v in doc["summary"].values()),
        "'summary' must map names to integers",
    )
    _need(doc["summary"] == summary, "'summary' does not match the cases")


@dataclass(frozen=True)
class RunFile:
    path: str
    sha256: str
    run: dict[str, Any]


def load_run(path: str | Path) -> RunFile:
    """Read and validate a saved run. Raises RunFileError with the reason."""
    path = Path(path)
    try:
        size = path.stat().st_size
        if size > MAX_RUN_BYTES:
            raise RunFileError(f"{path}: {size} bytes; the limit is {MAX_RUN_BYTES}")
        raw = path.read_bytes()
    except OSError as exc:
        raise RunFileError(f"cannot read {path}: {exc.strerror}") from None
    try:
        doc = parse_json_document(raw.decode("utf-8"), MAX_RUN_DEPTH)
    except ValueError as exc:
        raise RunFileError(f"{path}: not a valid run file: {exc}") from None
    try:
        validate_run(doc)
    except RunFileError as exc:
        raise RunFileError(f"{path}: {exc}") from None
    return RunFile(str(path), hashlib.sha256(raw).hexdigest(), doc)
