"""Command-line interface: ``planlens-agent``.

Exit codes for ``check`` and ``inspect``:

* 0 - the plan passes every supported required check;
* 1 - the plan is rejected;
* 2 - invalid input or configuration, unsupported required semantics, or a
  usage error.

Exit codes for ``suite run``: 0 every case passes, 1 analysis finished and a case
is rejected, 2 invalid input or an incomplete run. For ``suite diff``: 0 no new
violations in a like-for-like, fully accounted comparison, 1 new violations, 2 the
runs are invalid, not comparable, or the comparison is incomplete. Neither is an
approval; see docs/suites.md.

With ``--format json`` the report is the only thing written to stdout.
Operational messages go to stderr. Output never contains ANSI escapes.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
from collections.abc import Iterable
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import TextIO

from ._version import __version__
from .checker import check
from .compare import compare_runs, diff_to_json, render_diff
from .diagnostics import RULES
from .fixtures import FixtureFileError, render_fixture_run, run_fixtures
from .inspector import render_html
from .policy import validate_policy
from .report import safe
from .sarif import to_sarif_json
from .suite import (
    MAX_RUN_BYTES,
    RunFileError,
    SuiteFileError,
    load_run,
    load_suite,
    render_run,
    run_suite,
    run_to_json,
    suite_inputs,
    validate_labels,
)
from .view_model import build_view

TEMPLATES = ("release-basic",)


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _err(msg: str) -> None:
    print(f"planlens-agent: {msg}", file=sys.stderr)


def _write_json(obj: object, out: TextIO) -> None:
    out.write(json.dumps(obj, indent=2, ensure_ascii=True) + "\n")


def _exists(path: str) -> bool:
    try:
        return Path(path).exists()
    except OSError:
        return False


def _is_dir(path: str) -> bool:
    try:
        return Path(path).is_dir()
    except OSError:
        return False


def _save(path: str, text: str, what: str) -> bool:
    try:
        Path(path).write_text(text, encoding="utf-8")
    except OSError as exc:
        _err(f"cannot write {what} to {path}: {exc.strerror}")
        return False
    return True


def _output_would_replace_an_input(
    output: str, inputs: Iterable[tuple[str, str | Path]], what: str, verb: str
) -> bool:
    """Report and return True if ``output`` cannot safely be written.

    Compares file identity rather than names, so relative forms, symlinks, hard links and
    case-insensitive spellings of an input all count as that input. An input that does not
    exist yet is matched by its path. ``--overwrite`` never reaches this decision.
    """
    try:
        target: os.stat_result | None = os.stat(output)
    except (FileNotFoundError, NotADirectoryError):
        target = None
    except OSError as exc:
        _err(f"cannot write {what} to {output}: {exc.strerror}")
        return True
    wanted = os.path.normcase(os.path.abspath(output))
    for kind, name in inputs:
        try:
            if target is not None:
                same = os.path.samestat(target, os.stat(name))
            else:
                same = wanted == os.path.normcase(os.path.abspath(name))
        except (OSError, ValueError):
            continue  # reading that input reports the problem
        if same:
            _err(
                f"--output {safe(output)} is the input {kind} {safe(str(name))}; nothing was "
                f"{verb} or written. Choose a different output file."
            )
            return True
    return False


def _check_inputs(a: argparse.Namespace) -> tuple[tuple[str, str], ...]:
    return (("plan", a.plan), ("contract", a.contract), ("catalog", a.catalog))


def cmd_check(a: argparse.Namespace) -> int:
    what = "SARIF" if a.format == "sarif" else "report"
    if a.output and _output_would_replace_an_input(a.output, _check_inputs(a), what, "checked"):
        return 2
    result = check(a.plan, a.contract, a.catalog)
    if a.format == "sarif":
        if not _is_dir(a.source_root):
            _err(f"--source-root {a.source_root} is not a directory")
            return 2
        # SARIF mode: --output saves exactly what is printed.
        sarif = to_sarif_json(result, source_root=a.source_root)
        if a.output and not _save(a.output, sarif, "SARIF"):
            return 2
        sys.stdout.write(sarif)
        return result.exit_code
    # text and json modes: --output always saves the JSON report (unchanged since 0.1.0.dev0).
    if a.output and not _save(a.output, result.to_json(), "report"):
        return 2
    if a.format == "json":
        sys.stdout.write(result.to_json())
    else:
        sys.stdout.write(result.render_text())
    return result.exit_code


def cmd_inspect(a: argparse.Namespace) -> int:
    if _output_would_replace_an_input(a.output, _check_inputs(a), "inspector report", "checked"):
        return 2
    result = check(a.plan, a.contract, a.catalog)
    if not _save(a.output, render_html(build_view(result)), "inspector report"):
        return 2
    n = len(result.diagnostics)
    label = {"pass": "PASS", "reject": "REJECTED", "error": "ERROR"}[result.verdict]
    print(
        f"wrote {a.output}: {label} (exit {result.exit_code}), {n} finding{'s' if n != 1 else ''}"
    )
    return result.exit_code


def cmd_validate_policy(a: argparse.Namespace) -> int:
    policy = validate_policy(a.contract, a.catalog)
    code = 0 if not policy.diagnostics else 2
    if a.format == "json":
        _write_json(
            {
                "engine": {"name": "planlens-agent", "version": __version__},
                "valid": policy.usable,
                "enforceable": code == 0,
                "contract_sha256": policy.contract.digest if policy.contract else None,
                "catalog_sha256": policy.catalog.digest if policy.catalog else None,
                "diagnostics": [d.to_dict() for d in policy.diagnostics],
            },
            sys.stdout,
        )
        return code
    if code == 0 and policy.contract and policy.catalog:
        c, k = policy.contract, policy.catalog
        print(
            f"OK - contract {c.id} {c.version} is valid against catalog {k.id} {k.version}: "
            f"{_n(len(c.required_actions), 'required action')}, "
            f"{_n(len(c.prerequisites), 'prerequisite')}, "
            f"{_n(len(c.same_artifact), 'artifact-identity rule')}."
        )
        return 0
    print(
        f"POLICY NOT ENFORCEABLE - {len(policy.diagnostics)} finding(s); no plan can pass "
        "until these are resolved"
    )
    for d in policy.diagnostics:
        loc = f"{d.location.document}:{d.location.path or '/'}" if d.location else "-"
        print(f"  [{d.category}] {d.rule_id} at {loc}")
        print(f"    {safe(d.message)}")
        if d.suggestion:
            print(f"    next: {safe(d.suggestion)}")
    return code


def cmd_regress(a: argparse.Namespace) -> int:
    try:
        run = run_fixtures(a.fixtures)
    except FixtureFileError as exc:
        _err(f"fixture file is invalid: {exc}")
        return 2
    if a.format == "json":
        _write_json(run.to_dict(), sys.stdout)
    else:
        sys.stdout.write(render_fixture_run(run))
    return 0 if run.all_met else 1


def _parse_labels(pairs: list[str]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for pair in pairs:
        key, separator, value = pair.partition("=")
        if not separator:
            raise ValueError(f"--label {pair!r} must have the form name=value")
        labels[key] = value
    return validate_labels(labels)


def cmd_suite_run(a: argparse.Namespace) -> int:
    try:
        labels = _parse_labels(a.label)
    except ValueError as exc:
        _err(f"invalid label: {exc}")
        return 2
    try:
        suite = load_suite(a.suite)
    except SuiteFileError as exc:
        _err(f"suite file is invalid: {exc}")
        return 2
    if a.output:
        inputs = [("suite manifest", a.suite), *suite_inputs(suite, a.plans_dir)]
        if _output_would_replace_an_input(a.output, inputs, "run", "run"):
            return 2
        if _exists(a.output) and not a.overwrite:
            _err(
                f"{a.output} already exists; nothing was run. Baselines are reviewed inputs, so "
                "choose a new file, or pass --overwrite if replacing it is intended."
            )
            return 2
    run = run_suite(suite, a.plans_dir, labels)
    text = run_to_json(run)
    if len(text) > MAX_RUN_BYTES:
        _err(
            f"the run is {len(text)} bytes; the limit is {MAX_RUN_BYTES}. Nothing was written. "
            "Split the suite."
        )
        return 2
    if a.output and not _save(a.output, text, "run"):
        return 2
    sys.stdout.write(text if a.format == "json" else render_run(run))
    return int(run["exit_code"])


def cmd_suite_diff(a: argparse.Namespace) -> int:
    inputs = (("run", a.baseline), ("run", a.candidate))
    if a.output and _output_would_replace_an_input(a.output, inputs, "comparison", "compared"):
        return 2
    try:
        diff = compare_runs(load_run(a.baseline), load_run(a.candidate))
    except RunFileError as exc:
        _err(f"cannot compare: {exc}")
        return 2
    text = diff_to_json(diff)
    if a.output and not _save(a.output, text, "comparison"):
        return 2
    sys.stdout.write(text if a.format == "json" else render_diff(diff))
    return int(diff["exit_code"])


def cmd_rules(a: argparse.Namespace) -> int:
    width = max(len(r) for r in RULES)
    for rid in sorted(RULES):
        info = RULES[rid]
        print(f"{rid:<{width}}  {info.category:<16}  {info.title}")
    return 0


def cmd_explain(a: argparse.Namespace) -> int:
    info = RULES.get(a.rule)
    if info is None:
        near = difflib.get_close_matches(a.rule, list(RULES), n=3)
        _err(
            f"unknown rule {a.rule!r}"
            + (f"; did you mean {', '.join(near)}?" if near else "")
            + " Run 'planlens-agent rules' for the full list."
        )
        return 2
    print(f"{info.id}  (rule version {info.version}, category {info.category})")
    print(info.title)
    print()
    print(info.summary)
    if info.bad_example:
        print(f"\nExample that triggers it:\n  {info.bad_example}")
    if info.good_example:
        print(f"\nExample that satisfies it:\n  {info.good_example}")
    if info.limitations:
        print(f"\nLimits:\n  {info.limitations}")
    return 0


def _copy_tree(src: Traversable, dst: Path) -> list[Path]:
    written: list[Path] = []
    dst.mkdir(parents=True, exist_ok=True)
    for item in sorted(src.iterdir(), key=lambda t: t.name):
        if item.name.startswith(("__", ".")):
            continue
        target = dst / item.name
        if item.is_dir():
            written += _copy_tree(item, target)
        else:
            target.write_bytes(item.read_bytes())
            written.append(target)
    return written


def cmd_init(a: argparse.Namespace) -> int:
    target = Path(a.directory if a.directory is not None else a.template)
    src = resources.files("planlens_agent").joinpath("templates", a.template.replace("-", "_"))
    try:
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            _err(f"{target} already exists and is not an empty directory; nothing was written")
            return 2
        written = _copy_tree(src, target)
    except OSError as exc:
        _err(f"cannot create {target}: {exc.strerror}")
        return 2
    print(f"Created {a.template} template in {target} ({len(written)} files).")
    print("These are authored example files for learning the tool, not observed agent output.")
    print("\nNext:")
    print(f"  cd {target}")
    print("  planlens-agent validate-policy")
    print("  planlens-agent check plans/valid.json")
    print("  planlens-agent check plans/reversed-review.json")
    print("  cp plans/reversed-review.json my-plan.json   # fix the copy, then:")
    print("  planlens-agent check my-plan.json")
    print("  planlens-agent regress fixtures.toml")
    print("  planlens-agent suite run suite.toml --output baseline.run.json")
    print("  planlens-agent suite run suite.toml --plans-dir candidates/v2 --output v2.run.json")
    print("  planlens-agent suite diff baseline.run.json v2.run.json")
    print("Leave plans/ unchanged: fixtures.toml records those files as expected outcomes.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="planlens-agent",
        description="Lint AI-agent plans before they run. Offline; no model account needed.",
    )
    p.add_argument("--version", action="version", version=f"planlens-agent {__version__}")
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def policy_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--contract",
            default="contract.toml",
            help="owner contract (.toml or .json); default: contract.toml",
        )
        sp.add_argument(
            "--catalog",
            default="catalog.toml",
            help="owner action catalog (.toml or .json); default: catalog.toml",
        )

    c = sub.add_parser("check", help="check a candidate plan (exit 0 pass, 1 rejected, 2 error)")
    c.add_argument("plan", help="candidate plan (.json)")
    policy_args(c)
    c.add_argument("--format", choices=("text", "json", "sarif"), default="text")
    c.add_argument(
        "--output",
        metavar="FILE",
        help="text/json: also save the JSON report to FILE; sarif: save the SARIF log "
        "printed to stdout",
    )
    c.add_argument(
        "--source-root",
        metavar="DIR",
        default=".",
        help="sarif only: directory that file locations are made relative to (SRCROOT); "
        "default: the current directory",
    )
    c.set_defaults(func=cmd_check)

    n = sub.add_parser(
        "inspect", help="write a self-contained HTML report of one check (exit 0 / 1 / 2)"
    )
    n.add_argument("plan", help="candidate plan (.json)")
    policy_args(n)
    n.add_argument("--output", metavar="FILE", required=True, help="HTML file to write")
    n.set_defaults(func=cmd_inspect)

    v = sub.add_parser("validate-policy", help="validate the contract and catalog (exit 0 or 2)")
    policy_args(v)
    v.add_argument("--format", choices=("text", "json"), default="text")
    v.set_defaults(func=cmd_validate_policy)

    r = sub.add_parser("regress", help="run regression fixtures (exit 0 all met, 1 unmet, 2 error)")
    r.add_argument("fixtures", help="fixture file (.toml or .json)")
    r.add_argument("--format", choices=("text", "json"), default="text")
    r.set_defaults(func=cmd_regress)

    t = sub.add_parser("suite", help="run candidate plans as a suite and compare saved runs")
    suite_sub = t.add_subparsers(dest="suite_command", required=True, metavar="SUBCOMMAND")
    sr = suite_sub.add_parser(
        "run", help="check every case and save the run (exit 0 passed, 1 rejected, 2 incomplete)"
    )
    sr.add_argument("suite", help="suite file (.toml or .json)")
    sr.add_argument("--plans-dir", metavar="DIR", help="read candidate plans from DIR instead")
    sr.add_argument(
        "--label",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="record a caller-supplied label, such as model=x; not verified",
    )
    sr.add_argument("--output", metavar="FILE", help="save the run to FILE")
    sr.add_argument(
        "--overwrite",
        action="store_true",
        help="allow replacing an earlier --output file; an input is never replaced",
    )
    sr.add_argument("--format", choices=("text", "json"), default="text")
    sr.set_defaults(func=cmd_suite_run)
    sd = suite_sub.add_parser(
        "diff", help="compare two saved runs (exit 0 nothing new, 1 new, 2 cannot compare)"
    )
    sd.add_argument("baseline", help="baseline run file")
    sd.add_argument("candidate", help="candidate run file")
    sd.add_argument("--output", metavar="FILE", help="also save the comparison as JSON")
    sd.add_argument("--format", choices=("text", "json"), default="text")
    sd.set_defaults(func=cmd_suite_diff)

    i = sub.add_parser("init", help="copy a bundled example into a new directory")
    i.add_argument("template", nargs="?", default="release-basic", choices=TEMPLATES)
    i.add_argument("directory", nargs="?", help="target directory (default: template name)")
    i.set_defaults(func=cmd_init)

    sub.add_parser("rules", help="list every rule").set_defaults(func=cmd_rules)
    e = sub.add_parser("explain", help="explain a rule, offline")
    e.add_argument("rule")
    e.set_defaults(func=cmd_explain)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        _err("interrupted")
        return 2
    except BrokenPipeError:
        return 2
    except Exception as exc:  # a bug, not a user error: never report it as a pass
        _err(
            f"internal error ({type(exc).__name__}: {exc}). This is a bug in planlens-agent; "
            "no verdict was produced."
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
