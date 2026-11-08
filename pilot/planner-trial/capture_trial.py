"""Capture one planner trial through the LangGraph gate, keeping every input and output.

Run with planlens-agent and examples/langgraph_gate/requirements.lock installed. See README.md
in this folder. Handlers are dry: they only record calls, so no real action runs.

This tool never calls a model itself. A planner runs only if you pass --planner, and the origin
and identity recorded are what the operator declares. Nothing here verifies them.

Exit status follows the gate: 0 executed, 1 rejected, 2 anything else or invalid input.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import platform
import re
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from planlens_agent import __version__, validate_policy
from planlens_agent.loading import MAX_DEPTH, canonical_bytes, digest, parse_json_document
from planlens_agent.runner import StepCall
from planlens_agent.suite import evaluator_digest


def _gate_dir() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "examples" / "langgraph_gate"
        if (candidate / "gate.py").is_file():
            return candidate
    raise SystemExit("cannot find examples/langgraph_gate/gate.py above this script")


sys.path.insert(0, str(_gate_dir()))
from gate import EXIT, build_gate  # noqa: E402

TRIAL_SCHEMA = "planlens-agent/pilot-trial/v1"
ORIGINS = ("real-planner", "authored-example")
SECRET_NAME = re.compile(
    r"api[_-]?key|access[_-]?token|auth[_-]?token|bearer|secret|password|credential|authorization",
    re.I,
)


class SavedResponse:
    """A planner response the operator already has, returned as it is stored."""

    def __init__(self, path: Path) -> None:
        self.data = path.read_bytes()

    def __call__(self, request: str) -> str:
        return self.data.decode("utf-8")


def parse_strict(raw: Any) -> Mapping[str, Any]:
    """The default: valid JSON text or a mapping, and nothing repaired."""
    if isinstance(raw, Mapping):
        return raw
    if isinstance(raw, str):
        parsed: Mapping[str, Any] = parse_json_document(raw, MAX_DEPTH)
        return parsed
    raise TypeError(f"planner returned {type(raw).__name__}, not text or a mapping")


class Capture:
    """The recipe's Planner, keeping what the source returned and what conversion did."""

    def __init__(self, source: Callable[[str], Any], convert: Callable[[Any], Any]) -> None:
        self.source, self.convert = source, convert
        self.returned = False
        self.raw: Any = None
        self.converted: Any = None
        self.conversion_error: str | None = None

    def __call__(self, request: str) -> Any:
        self.raw = self.source(request)
        self.returned = True
        try:
            self.converted = self.convert(self.raw)
        except Exception as exc:
            self.conversion_error = f"{type(exc).__name__}: {exc}"
            raise
        return self.converted


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_function(path: Path, name: str) -> Callable[..., Any]:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    function = getattr(module, name, None)
    if not callable(function):
        raise SystemExit(f"{path} has no callable {name!r}")
    return function  # type: ignore[no-any-return]


def _secret_names(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if SECRET_NAME.search(str(key)):
                found.append(str(key))
            found += _secret_names(item)
    elif isinstance(value, list):
        for item in value:
            found += _secret_names(item)
    return found


def _raw_record(capture: Capture, source: Any) -> tuple[str, bytes | None, str]:
    """(file name, content, description) of what the planner returned."""
    if isinstance(source, SavedResponse):
        return "raw_output.txt", source.data, "saved response, stored byte for byte"
    if not capture.returned:
        return "raw_output.txt", None, "none: the planner raised before returning"
    raw = capture.raw
    if isinstance(raw, str):
        return "raw_output.txt", raw.encode("utf-8", "surrogatepass"), "text, as returned"
    if isinstance(raw, Mapping):
        text = json.dumps(raw, indent=2, ensure_ascii=False, default=repr)
        return (
            "raw_output.json",
            text.encode("utf-8", "surrogatepass"),
            "parsed data: the callable returned a mapping, so its original text is not available",
        )
    return "raw_output.txt", repr(raw).encode(), f"repr of a {type(raw).__name__}"


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--origin", required=True, choices=ORIGINS, help="declared by you")
    parser.add_argument("--request", required=True, type=Path, help="exact request text")
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--raw-response", type=Path, help="a response you already have")
    source.add_argument("--planner", help="FILE.py:function taking the request, returning output")
    parser.add_argument("--convert", type=Path, help="FILE.py defining convert(raw) -> mapping")
    parser.add_argument("--planner-info", type=Path, help="identity and settings actually known")
    parser.add_argument("--label", default=None, help="what this trial is, stored as given")
    parser.add_argument("--out", required=True, type=Path, help="new directory for this trial")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    a = _arguments(argv)

    def refuse(message: str) -> int:
        print(f"capture_trial: {message}; nothing was written", file=sys.stderr)
        return 2

    if a.out.exists():
        return refuse(f"{a.out} already exists, and a trial record is never replaced")
    try:
        request_bytes = a.request.read_bytes()
        request = request_bytes.decode("utf-8")
        contract_bytes, catalog_bytes = a.contract.read_bytes(), a.catalog.read_bytes()
        info = json.loads(a.planner_info.read_text("utf-8")) if a.planner_info else None
        saved = SavedResponse(a.raw_response) if a.raw_response else None
        convert_source = a.convert.read_bytes() if a.convert else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return refuse(f"cannot read an input: {exc}")
    if info is not None and _secret_names(info):
        return refuse(f"--planner-info has credential-like fields {_secret_names(info)}")

    policy = validate_policy(a.contract, a.catalog)
    if policy.diagnostics or policy.catalog is None:
        return refuse(f"the policy is not enforceable: {[d.rule_id for d in policy.diagnostics]}")
    if a.planner:
        file, _, name = a.planner.rpartition(":")
        if not file or not name:
            return refuse("--planner must be FILE.py:function")
        source: Any = _load_function(Path(file), name)
    else:
        source = saved
    convert = _load_function(a.convert, "convert") if a.convert else parse_strict

    a.out.mkdir(parents=True)
    policy_dir = a.out / "policy"
    policy_dir.mkdir()
    contract_copy = policy_dir / f"contract{a.contract.suffix}"
    catalog_copy = policy_dir / f"catalog{a.catalog.suffix}"
    contract_copy.write_bytes(contract_bytes)
    catalog_copy.write_bytes(catalog_bytes)
    (a.out / "request.txt").write_bytes(request_bytes)

    calls: list[str] = []
    actions = policy.catalog.actions

    def dry_handler(call: StepCall) -> dict[str, str]:
        calls.append(call.step_id)
        return {output: f"<dry run of {call.step_id}>" for output in actions[call.action].outputs}

    capture = Capture(source, convert)
    # The gate reads the preserved copies, so what was checked is what is on record.
    gate = build_gate(capture, contract_copy, catalog_copy, {x: dry_handler for x in actions})
    state = gate.invoke({"request": request})

    raw_name, raw_content, raw_kind = _raw_record(capture, source)
    if raw_content is not None:
        (a.out / raw_name).write_bytes(raw_content)
    try:
        output_sha = digest(capture.converted) if isinstance(capture.converted, Mapping) else None
    except (TypeError, ValueError):
        output_sha = None
    conversion: dict[str, Any] = {
        "applied": bool(a.convert),
        "description": "operator-supplied convert()" if a.convert else "none: strict JSON parse",
        "script": None,
        "script_sha256": None,
        "input_sha256": _sha(raw_content) if raw_content is not None else None,
        "output_canonical_sha256": output_sha,
        "error": capture.conversion_error,
    }
    if a.convert and convert_source is not None:
        conversion["script"] = "convert.py"
        conversion["script_sha256"] = _sha(convert_source)
        (a.out / "convert.py").write_bytes(convert_source)
    _write_json(a.out / "conversion.json", conversion)

    report = state.get("report")
    if report is not None:
        (a.out / "checked_plan.json").write_bytes(canonical_bytes(state["plan"]) + b"\n")
        _write_json(a.out / "report.json", report)
    _write_json(
        a.out / "planner.json",
        {
            "identity_status": "supplied by the operator, not verified"
            if info is not None
            else "not supplied",
            "identity": info,
        },
    )
    _write_json(
        a.out / "outcome.json",
        {
            "trial_schema": TRIAL_SCHEMA,
            "captured_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "origin": a.origin,
            "origin_status": "declared by the operator, not verified",
            "trial_label": a.label,
            "planner_source": "planner callable" if a.planner else "saved response file",
            "raw_output": {"file": raw_name if raw_content is not None else None, "kind": raw_kind},
            "status": state["status"],
            "reason": state.get("reason", ""),
            "verdict": report["verdict"] if report is not None else None,
            "findings": [d["rule_id"] for d in report["diagnostics"]] if report else [],
            "dry_run_handler_calls": calls,
            "note": "Handlers were dry: no real action ran.",
            "engine": {"version": __version__, "evaluator_sha256": evaluator_digest()},
            "langgraph_version": importlib.metadata.version("langgraph"),
            "python": platform.python_version(),
        },
    )
    entries = sorted(p for p in a.out.rglob("*") if p.is_file())
    _write_json(
        a.out / "manifest.json",
        {p.relative_to(a.out).as_posix(): _sha(p.read_bytes()) for p in entries},
    )
    print(f"origin:        {a.origin} (declared, not verified)")
    print(f"status:        {state['status']}")
    if state.get("reason"):
        print(f"reason:        {state['reason']}")
    print(f"dry-run calls: {len(calls)}")
    print(f"record:        {a.out}")
    return EXIT.get(state["status"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
