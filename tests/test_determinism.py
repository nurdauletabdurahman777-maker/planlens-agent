"""Determinism and offline operation (Standard section 3.5)."""

from __future__ import annotations

import socket
import subprocess
import sys

import pytest

from planlens_agent import ReferenceRunner, check, run_fixtures, validate_policy

from helpers import ACCEPTANCE, TEMPLATE, load_cases
from test_acceptance import _paths

CONTRACT, CATALOG = TEMPLATE / "contract.toml", TEMPLATE / "catalog.toml"


def test_reports_are_byte_identical_across_runs() -> None:
    _, cases = load_cases()
    for case in cases:
        first = check(*_paths(case)).to_json()
        assert all(check(*_paths(case)).to_json() == first for _ in range(3)), case["name"]


def test_reports_are_identical_across_processes() -> None:
    plan = str(TEMPLATE / "plans" / "artifact-mismatch.json")
    code = (
        "import sys; from planlens_agent import check; "
        f"sys.stdout.write(check({plan!r}, {str(CONTRACT)!r}, {str(CATALOG)!r}).to_json())"
    )
    outs = {
        subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={"PYTHONHASHSEED": seed},
        ).stdout
        for seed in ("0", "1", "12345")
    }
    assert len(outs) == 1


def test_reports_contain_no_clock_or_host_data() -> None:
    js = check(TEMPLATE / "plans" / "valid.json", CONTRACT, CATALOG).to_json()
    for marker in ("timestamp", "created", "hostname", "2026-", "T00:"):
        assert marker not in js


def test_everything_works_with_networking_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*a: object, **k: object) -> None:
        raise AssertionError("planlens-agent attempted to open a network socket")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    _, cases = load_cases()
    for case in cases:
        check(*_paths(case))
    assert validate_policy(CONTRACT, CATALOG).usable
    assert run_fixtures(TEMPLATE / "fixtures.toml").all_met
    handlers = {
        a: (lambda call: {"artifact": "x"} if call.action == "build" else None)
        for a in ("build", "unit_tests", "security_scan", "code_review", "deploy", "notify")
    }
    approval = check(TEMPLATE / "plans" / "valid.json", CONTRACT, CATALOG).approve()
    assert ReferenceRunner(CONTRACT, CATALOG, handlers).dispatch(approval).status == "completed"
    assert ACCEPTANCE.is_dir()
