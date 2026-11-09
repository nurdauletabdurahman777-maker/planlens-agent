"""The CI gate blocks publication unless GitHub's API proves CI passed for the exact commit.

Responses are served over HTTP by a local mock with the field names, job names and re-run shape
of real responses from this repository's Actions API. The real script runs as a subprocess.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from helpers import ROOT

SCRIPT = ROOT / "scripts" / "ci_gate.py"
PUBLISH = ROOT / ".github" / "workflows" / "publish.yml"
spec = importlib.util.spec_from_file_location("ci_gate", SCRIPT)
assert spec and spec.loader
ci_gate = importlib.util.module_from_spec(spec)
sys.modules["ci_gate"] = ci_gate
spec.loader.exec_module(ci_gate)

REPO = ci_gate.REPOSITORY
COMMIT = "a" * 40
OTHER = "b" * 40
CI_ID, DEMO_ID = 411329662, 411329663
TOKEN = "test-token-not-a-credential"
posix_only = pytest.mark.skipif(os.name == "nt", reason="runs the workflow step with bash")


def make_run(number: int = 10, **changes: Any) -> dict[str, Any]:
    run: dict[str, Any] = {
        "id": 42670000000 + number,
        "name": "CI",
        "path": ".github/workflows/ci.yml",
        "workflow_id": CI_ID,
        "head_sha": COMMIT,
        "head_branch": "main",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "run_number": number,
        "run_attempt": 1,
        "pull_requests": [],
        "previous_attempt_url": None,
        "repository": {"full_name": REPO},
        "head_repository": {"full_name": REPO},
    }
    return {**run, **changes}


def make_jobs(run: dict[str, Any], **changes: Any) -> list[dict[str, Any]]:
    return [
        {
            "id": 129000000000 + run["id"] * 100 + run["run_attempt"] * 50 + index,
            "run_id": run["id"],
            "run_attempt": run["run_attempt"],
            "head_sha": run["head_sha"],
            "head_branch": run["head_branch"],
            "workflow_name": "CI",
            "name": name,
            "status": "completed",
            "conclusion": "success",
            **changes,
        }
        for index, name in enumerate(sorted(ci_gate.REQUIRED_JOBS))
    ]


class Mock:
    """What the API currently says. Tests change it to describe a situation."""

    def __init__(self) -> None:
        self.workflow = {
            "id": CI_ID,
            "name": "CI",
            "path": ".github/workflows/ci.yml",
            "state": "active",
        }
        self.runs: list[dict[str, Any]] = []
        self.jobs: dict[tuple[int, int], list[dict[str, Any]]] = {}
        self.page_size = 100
        self.status_for: dict[str, int] = {}
        self.raw_for: dict[str, bytes] = {}
        self.total_override: dict[str, int] = {}
        self.after_jobs: list[dict[str, Any]] | None = None
        self.requests: list[str] = []
        self.tokens: set[str] = set()

    def add(self, run: dict[str, Any], jobs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        self.runs.append(run)
        self.jobs[(run["id"], run["run_attempt"])] = make_jobs(run) if jobs is None else jobs
        return run


@pytest.fixture
def api() -> Iterator[tuple[Mock, str]]:
    mock = Mock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            return

        def send(self, code: int, body: bytes, headers: dict[str, str] | None = None) -> None:
            self.send_response(code)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def page(self, key: str, items: list[dict[str, Any]], path: str, q: dict[str, Any]) -> None:
            per_page = min(int(q["per_page"][0]), mock.page_size)
            start = (int(q["page"][0]) - 1) * per_page
            total = mock.total_override.get(key, len(items))
            body = {"total_count": total, key: items[start : start + per_page]}
            self.send(200, json.dumps(body).encode())

        def do_GET(self) -> None:
            split = urlsplit(self.path)
            path, q = split.path, parse_qs(split.query)
            mock.requests.append(self.path)
            mock.tokens.add(self.headers.get("Authorization", ""))
            if path in mock.status_for:
                code = mock.status_for[path]
                self.send(code, b"{}", {"Location": "http://127.0.0.1:1/x"} if code == 302 else {})
            elif path in mock.raw_for:
                self.send(200, mock.raw_for[path])
            elif path == f"/repos/{REPO}/actions/workflows/ci.yml":
                self.send(200, json.dumps(mock.workflow).encode())
            elif match := re.fullmatch(rf"/repos/{REPO}/actions/workflows/(\d+)/runs", path):
                assert match.group(1) == str(CI_ID) and q["head_sha"] == [COMMIT]
                self.page("workflow_runs", mock.runs, path, q)
            elif match := re.fullmatch(
                rf"/repos/{REPO}/actions/runs/(\d+)/attempts/(\d+)/jobs", path
            ):
                self.page("jobs", mock.jobs[(int(match[1]), int(match[2]))], path, q)
                if mock.after_jobs is not None:
                    mock.runs, mock.after_jobs = mock.after_jobs, None
            else:
                self.send(404, b'{"message": "Not Found"}')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield mock, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def environment(base: str, **changes: Any) -> dict[str, str]:
    values = {
        "PATH": os.environ["PATH"],
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "GITHUB_API_URL": base,
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_TOKEN": TOKEN,
        **changes,
    }
    return {k: v for k, v in values.items() if v is not None}


def gate(base: str, commit: str = COMMIT, **env: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--commit", commit],
        env=environment(base, **env),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def assert_blocked(done: subprocess.CompletedProcess[str], reason: str) -> None:
    assert done.returncode == 1, done.stdout + done.stderr
    assert done.stdout.startswith("BLOCKED:"), done.stdout + done.stderr
    assert reason in done.stdout, done.stdout
    assert "CI passed" not in done.stdout
    assert TOKEN not in done.stdout + done.stderr


Api = tuple[Mock, str]


# ---- The situations in which publication may proceed


def test_a_green_run_on_main_for_the_commit_passes(api: Api) -> None:
    mock, base = api
    mock.add(make_run())
    done = gate(base)
    assert done.returncode == 0, done.stdout
    assert f"CI passed for {COMMIT}" in done.stdout
    assert mock.tokens == {f"Bearer {TOKEN}"}


def test_a_failed_attempt_followed_by_a_successful_rerun_passes(api: Api) -> None:
    mock, base = api
    first = make_run(run_attempt=1, conclusion="failure")
    second = make_run(run_attempt=2, previous_attempt_url="https://example.invalid/attempts/1")
    mock.jobs[(first["id"], 1)] = make_jobs(first, conclusion="cancelled")
    mock.add(second)
    done = gate(base)
    assert done.returncode == 0, done.stdout
    assert any("/attempts/2/jobs" in r for r in mock.requests)
    assert not any("/attempts/1/jobs" in r for r in mock.requests)


def test_a_newer_success_supersedes_an_older_failure_in_the_same_group(api: Api) -> None:
    mock, base = api
    mock.add(make_run(9, conclusion="failure"), [])
    mock.add(make_run(10))
    done = gate(base)
    assert done.returncode == 0, done.stdout
    assert "superseded older runs: 1" in done.stdout


def test_every_ref_of_the_commit_can_be_green_together(api: Api) -> None:
    mock, base = api
    mock.add(make_run(8, head_branch="release/prepare-0.1.0"))
    mock.add(make_run(9))
    mock.add(make_run(10, head_branch="v0.1.0"))
    done = gate(base)
    assert done.returncode == 0, done.stdout
    assert done.stdout.count("verified:") == 3


@pytest.mark.parametrize(
    "ignored",
    [
        pytest.param({"workflow_id": DEMO_ID, "path": ".github/workflows/x.yml"}, id="workflow"),
        pytest.param({"head_sha": OTHER}, id="commit"),
        pytest.param({"head_repository": {"full_name": "someone/fork"}}, id="fork"),
        pytest.param({"head_repository": None}, id="deleted-fork"),
    ],
)
def test_unrelated_failures_do_not_block_a_green_commit(api: Api, ignored: dict[str, Any]) -> None:
    mock, base = api
    mock.add(make_run(20, conclusion="failure", **ignored), [])
    mock.add(make_run(10))
    done = gate(base)
    assert done.returncode == 0, done.stdout
    assert "ignored runs: 1" in done.stdout


def test_runs_and_jobs_are_read_across_every_page(api: Api) -> None:
    mock, base = api
    for number in range(1, 8):
        mock.add(make_run(number), [])
    mock.jobs[(make_run(7)["id"], 1)] = make_jobs(make_run(7))
    mock.page_size = 3
    done = gate(base)
    assert done.returncode == 0, done.stdout
    assert any("/runs?" in r and "page=3" in r for r in mock.requests)
    assert any("/jobs?" in r and "page=10" in r for r in mock.requests)


def test_a_failure_on_the_last_page_is_found(api: Api) -> None:
    mock, base = api
    for number in range(1, 7):
        mock.add(make_run(number, head_branch=f"branch-{number}"))
    mock.add(make_run(7))
    mock.add(make_run(8, head_branch="v0.1.0", conclusion="failure"), [])
    mock.page_size = 3
    assert_blocked(gate(base), "did not succeed")


# ---- Negative controls: each must stop publication


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "requested", "pending"])
def test_a_run_that_has_not_completed_blocks(api: Api, status: str) -> None:
    mock, base = api
    mock.add(make_run(status=status, conclusion=None))
    assert_blocked(gate(base), "not completed")


@pytest.mark.parametrize(
    "conclusion",
    ["failure", "cancelled", "timed_out", "skipped", "neutral", "action_required", "stale", None],
)
def test_a_completed_run_that_is_not_a_success_blocks(api: Api, conclusion: str | None) -> None:
    mock, base = api
    mock.add(make_run(conclusion=conclusion))
    assert_blocked(gate(base), "did not succeed")


def test_no_runs_at_all_blocks(api: Api) -> None:
    _, base = api
    assert_blocked(gate(base), "no CI run from a push to main")


@pytest.mark.parametrize(
    "elsewhere",
    [
        pytest.param({"head_branch": "release/prepare-0.1.0"}, id="another-branch"),
        pytest.param({"head_branch": "v0.1.0"}, id="tag"),
        pytest.param({"event": "pull_request"}, id="pull-request-event"),
        pytest.param({"event": "workflow_dispatch"}, id="manual-event"),
        pytest.param({"head_branch": None}, id="no-ref"),
    ],
)
def test_success_only_outside_a_push_to_main_blocks(api: Api, elsewhere: dict[str, Any]) -> None:
    mock, base = api
    mock.add(make_run(**elsewhere))
    assert_blocked(gate(base), "no CI run from a push to main")


@pytest.mark.parametrize(
    "unrelated",
    [
        pytest.param({"workflow_id": DEMO_ID, "path": ".github/workflows/x.yml"}, id="workflow"),
        pytest.param({"workflow_id": DEMO_ID}, id="workflow-id-only"),
        pytest.param({"path": ".github/workflows/ci-copy.yml"}, id="workflow-path-only"),
        pytest.param({"head_sha": OTHER}, id="commit"),
        pytest.param({"head_repository": {"full_name": "someone/fork"}}, id="fork"),
    ],
)
def test_an_unrelated_success_cannot_satisfy_the_gate(api: Api, unrelated: dict[str, Any]) -> None:
    mock, base = api
    mock.add(make_run(**unrelated))
    assert_blocked(gate(base), "no CI run from a push to main")


@pytest.mark.parametrize(
    "newer",
    [
        pytest.param({"conclusion": "failure"}, id="failed"),
        pytest.param({"conclusion": "cancelled"}, id="cancelled"),
        pytest.param({"status": "in_progress", "conclusion": None}, id="in-progress"),
        pytest.param({"status": "queued", "conclusion": None}, id="queued"),
    ],
)
def test_an_older_success_cannot_conceal_a_newer_run(api: Api, newer: dict[str, Any]) -> None:
    mock, base = api
    mock.add(make_run(9))
    mock.add(make_run(10, **newer))
    assert_blocked(gate(base), "run 42670000010")


def test_the_newest_run_decides_whatever_order_the_api_returns(api: Api) -> None:
    mock, base = api
    mock.add(make_run(10, conclusion="failure"))
    mock.add(make_run(9))
    assert_blocked(gate(base), "did not succeed")


def test_an_older_run_being_rerun_blocks_even_under_a_newer_success(api: Api) -> None:
    mock, base = api
    mock.add(make_run(9, run_attempt=2, status="in_progress", conclusion=None))
    mock.add(make_run(10))
    assert_blocked(gate(base), "not completed")


@pytest.mark.parametrize(
    "attempt",
    [
        pytest.param({"conclusion": "failure"}, id="failed"),
        pytest.param({"status": "in_progress", "conclusion": None}, id="in-progress"),
    ],
)
def test_a_rerun_cannot_hide_behind_an_earlier_passing_attempt(
    api: Api, attempt: dict[str, Any]
) -> None:
    mock, base = api
    passed = make_run(run_attempt=1)
    mock.jobs[(passed["id"], 1)] = make_jobs(passed)
    mock.add(make_run(run_attempt=2, **attempt))
    assert_blocked(gate(base), "attempt 2")


@pytest.mark.parametrize(
    "ref",
    [
        pytest.param("v0.1.0", id="tag"),
        pytest.param("release/prepare-0.1.0", id="release-branch"),
    ],
)
@pytest.mark.parametrize(
    "state",
    [
        pytest.param({"conclusion": "failure"}, id="failed"),
        pytest.param({"status": "queued", "conclusion": None}, id="queued"),
    ],
)
def test_a_bad_run_on_another_ref_of_the_commit_blocks(
    api: Api, ref: str, state: dict[str, Any]
) -> None:
    mock, base = api
    mock.add(make_run(9))
    mock.add(make_run(10, head_branch=ref, **state), [])
    assert_blocked(gate(base), ref)


@pytest.mark.parametrize(
    ("status", "conclusion"),
    [
        ("completed", "failure"),
        ("completed", "cancelled"),
        ("completed", "skipped"),
        ("completed", "neutral"),
        ("completed", "timed_out"),
        ("in_progress", None),
        ("queued", None),
    ],
)
def test_one_bad_job_blocks_a_run_reported_as_success(
    api: Api, status: str, conclusion: str | None
) -> None:
    mock, base = api
    run = mock.add(make_run())
    mock.jobs[(run["id"], 1)][3].update(status=status, conclusion=conclusion)
    assert_blocked(gate(base), "job did not succeed")


def test_a_missing_required_job_blocks(api: Api) -> None:
    mock, base = api
    run = mock.add(make_run())
    removed = mock.jobs[(run["id"], 1)].pop(0)
    assert_blocked(gate(base), f"required job is missing: {removed['name']}")


def test_no_jobs_at_all_blocks(api: Api) -> None:
    mock, base = api
    mock.add(make_run(), [])
    assert_blocked(gate(base), "required job is missing")


def test_an_undeclared_job_blocks(api: Api) -> None:
    mock, base = api
    run = mock.add(make_run())
    mock.jobs[(run["id"], 1)].append({**mock.jobs[(run["id"], 1)][0], "id": 1, "name": "extra"})
    assert_blocked(gate(base), "not declared by this gate: extra")


def test_a_duplicated_job_blocks(api: Api) -> None:
    mock, base = api
    run = mock.add(make_run())
    mock.jobs[(run["id"], 1)].append({**mock.jobs[(run["id"], 1)][0], "id": 1})
    assert_blocked(gate(base), "job appears twice")


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"run_id": 1}, "another run"),
        ({"run_attempt": 7}, "another attempt"),
        ({"head_sha": OTHER}, "another commit"),
    ],
)
def test_a_job_from_elsewhere_blocks(api: Api, change: dict[str, Any], reason: str) -> None:
    mock, base = api
    run = mock.add(make_run())
    mock.jobs[(run["id"], 1)][0].update(change)
    assert_blocked(gate(base), reason)


def test_a_rerun_starting_during_the_check_blocks(api: Api) -> None:
    mock, base = api
    run = mock.add(make_run())
    mock.after_jobs = [{**run, "run_attempt": 2, "status": "in_progress", "conclusion": None}]
    assert_blocked(gate(base), "changed while it was being checked")


def test_a_new_run_appearing_during_the_check_blocks(api: Api) -> None:
    mock, base = api
    run = mock.add(make_run())
    mock.after_jobs = [run, make_run(11, status="queued", conclusion=None)]
    assert_blocked(gate(base), "changed while it was being checked")


def test_a_run_of_another_repository_blocks(api: Api) -> None:
    mock, base = api
    mock.add(make_run(repository={"full_name": "someone/else"}))
    assert_blocked(gate(base), "another repository")


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"state": "disabled_manually"}, "disabled_manually"),
        ({"path": ".github/workflows/other.yml"}, "not .github/workflows/ci.yml"),
    ],
)
def test_the_workflow_identity_is_checked(api: Api, change: dict[str, Any], reason: str) -> None:
    mock, base = api
    mock.add(make_run())
    mock.workflow.update(change)
    assert_blocked(gate(base), reason)


@pytest.mark.parametrize("name", ["run_attempt", "run_number", "event", "status", "workflow_id"])
def test_a_malformed_run_blocks(api: Api, name: str) -> None:
    mock, base = api
    run = mock.add(make_run())
    del run[name]
    assert_blocked(gate(base), name)


@pytest.mark.parametrize("name", ["run_attempt", "run_id", "head_sha", "name", "status"])
def test_a_malformed_job_blocks(api: Api, name: str) -> None:
    mock, base = api
    run = mock.add(make_run())
    del mock.jobs[(run["id"], 1)][0][name]
    assert_blocked(gate(base), name)


def test_a_boolean_is_not_a_number(api: Api) -> None:
    mock, base = api
    mock.add(make_run(run_attempt=True))
    assert_blocked(gate(base), "run_attempt")


def test_a_short_listing_blocks(api: Api) -> None:
    mock, base = api
    mock.add(make_run())
    mock.total_override["workflow_runs"] = 2
    assert_blocked(gate(base), "stopped at 1 of 2")


def test_a_short_job_listing_blocks(api: Api) -> None:
    mock, base = api
    mock.add(make_run())
    mock.total_override["jobs"] = 31
    assert_blocked(gate(base), "stopped at 30 of 31")


def test_a_listing_longer_than_its_total_blocks(api: Api) -> None:
    mock, base = api
    mock.add(make_run())
    mock.total_override["jobs"] = 29
    assert_blocked(gate(base), "for a total of 29")


def test_more_results_than_the_api_can_list_blocks(api: Api) -> None:
    mock, base = api
    mock.add(make_run())
    mock.total_override["workflow_runs"] = 1001
    assert_blocked(gate(base), "more than the API can list")


@pytest.mark.parametrize("code", [302, 401, 403, 404, 429, 500, 503])
def test_an_api_error_blocks(api: Api, code: int) -> None:
    mock, base = api
    mock.add(make_run())
    mock.status_for[f"/repos/{REPO}/actions/workflows/{CI_ID}/runs"] = code
    assert_blocked(gate(base), "redirect" if code == 302 else f"answered {code}")


def test_an_api_error_on_the_jobs_blocks(api: Api) -> None:
    mock, base = api
    run = mock.add(make_run())
    mock.status_for[f"/repos/{REPO}/actions/runs/{run['id']}/attempts/1/jobs"] = 500
    assert_blocked(gate(base), "answered 500")


@pytest.mark.parametrize("raw", [b"not json", b"[]", b"null", b'{"total_count": "1"}', b"{}"])
def test_an_unreadable_answer_blocks(api: Api, raw: bytes) -> None:
    mock, base = api
    mock.add(make_run())
    mock.raw_for[f"/repos/{REPO}/actions/workflows/{CI_ID}/runs"] = raw
    assert_blocked(gate(base), "")


def test_an_unreachable_api_blocks() -> None:
    assert_blocked(gate("http://127.0.0.1:9"), "no usable answer")


def test_a_missing_token_blocks(api: Api) -> None:
    _, base = api
    assert_blocked(gate(base, GITHUB_TOKEN=None), "GITHUB_TOKEN")


@pytest.mark.parametrize("repository", ["someone/fork", None])
def test_another_repository_blocks_before_any_request(api: Api, repository: str | None) -> None:
    mock, base = api
    assert_blocked(gate(base, GITHUB_REPOSITORY=repository), "this gate is for")
    assert mock.requests == []


@pytest.mark.parametrize("commit", ["abc123", "A" * 40, "g" * 40, "", "a" * 41])
def test_a_commit_that_is_not_a_full_sha_blocks(api: Api, commit: str) -> None:
    mock, base = api
    assert_blocked(gate(base, commit=commit), "full lowercase commit SHA")
    assert mock.requests == []


@pytest.mark.parametrize(
    "url", ["https://evil.example", "http://api.github.com", "http://127.0.0.1.evil.example:1"]
)
def test_the_token_is_only_sent_to_github(url: str) -> None:
    assert_blocked(gate(url), "refusing to send the token")


# ---- The gate is wired into the workflow so that a refusal stops the job


def steps(job: str) -> list[str]:
    return re.split(r"(?m)^      - ", job)[1:]


def job(name: str) -> str:
    text = PUBLISH.read_text(encoding="utf-8")
    match = re.search(rf"^  {name}:\n(.*?)(?=^  [a-z-]+:\n|\Z)", text, re.M | re.S)
    assert match, name
    return match.group(1)


def run_text(job_name: str, step_name: str) -> str:
    (step,) = [s for s in steps(job(job_name)) if s.startswith(f"name: {step_name}")]
    match = re.search(r"^        run: (.+)$", step, re.M)
    assert match, step
    return match.group(1)


def as_in_the_workflow(
    tmp_path: Path, base: str, command: str, cwd: Path
) -> tuple[subprocess.CompletedProcess[str], bool]:
    """Run a step's command as GitHub does, then see whether the job went on to the next step."""
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "python3").symlink_to(sys.executable)
    marker = tmp_path / "next-step-ran"
    script = tmp_path / "step.sh"
    script.write_text(f'{command}\ntouch "{marker}"\n', encoding="utf-8")
    done = subprocess.run(
        ["bash", "-e", str(script)],
        cwd=cwd,
        env={**environment(base, PATH=f"{tools}:/usr/bin:/bin"), "COMMIT": COMMIT},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return done, marker.exists()


@posix_only
def test_the_gate_step_stops_the_build_when_ci_failed(api: Api, tmp_path: Path) -> None:
    mock, base = api
    mock.add(make_run(conclusion="failure"))
    command = run_text("check", "Stop unless CI passed")
    done, progressed = as_in_the_workflow(tmp_path, base, command, ROOT)
    assert done.returncode == 1 and "BLOCKED" in done.stdout and not progressed


@posix_only
def test_the_gate_step_lets_the_build_start_when_ci_passed(api: Api, tmp_path: Path) -> None:
    mock, base = api
    mock.add(make_run())
    command = run_text("check", "Stop unless CI passed")
    done, progressed = as_in_the_workflow(tmp_path, base, command, ROOT)
    assert done.returncode == 0 and progressed, done.stdout + done.stderr


@posix_only
@pytest.mark.parametrize(
    ("state", "reason"),
    [
        pytest.param(
            {"run_attempt": 2, "status": "in_progress", "conclusion": None},
            "not completed",
            id="rerun-started-while-waiting-for-approval",
        ),
        pytest.param({"conclusion": "failure"}, "did not succeed", id="now-failed"),
    ],
)
def test_the_final_gate_stops_the_upload_with_its_own_copy(
    api: Api, tmp_path: Path, state: dict[str, Any], reason: str
) -> None:
    mock, base = api
    mock.add(make_run(**state))
    pair = tmp_path / "work" / "release-pair"
    pair.mkdir(parents=True)
    shutil.copy(SCRIPT, pair / "ci_gate.py")
    command = run_text("publish", "Stop unless CI still passes")
    done, progressed = as_in_the_workflow(tmp_path, base, command, tmp_path / "work")
    assert done.returncode == 1 and reason in done.stdout and not progressed


def test_ci_is_gated_before_building_and_again_just_before_upload() -> None:
    check, publish = job("check"), job("publish")
    assert (
        check.index("origin/main")
        < check.index("ci_gate.py --commit")
        < check.index("uv build")
        < check.index("upload-artifact")
    )
    assert check.index("cp scripts/ci_gate.py release-pair/") < check.index("upload-artifact")
    assert (
        publish.index("sha256sum --check")
        < publish.index("release-pair/ci_gate.py --commit")
        < publish.index("gh-action-pypi-publish")
    )
    assert "needs: check" in publish and "environment:" in publish


def test_the_gate_cannot_be_skipped_or_softened() -> None:
    for step in (*steps(job("check")), *steps(job("publish"))):
        if "ci_gate.py --commit" in step:
            assert "if:" not in step and "continue-on-error" not in step and "||" not in step


def test_the_gate_uses_only_the_permissions_it_needs() -> None:
    check, publish = job("check"), job("publish")
    assert "permissions:\n      contents: read\n      actions: read\n" in check
    assert "permissions:\n      actions: read\n      id-token: write\n" in publish
    text = PUBLISH.read_text(encoding="utf-8")
    assert text.count("GITHUB_TOKEN: ${{ github.token }}") == 2
    assert not re.search(r"^\s+(contents|actions|packages|deployments): write", text, re.M)


def matrix_values(block: str, name: str) -> list[str]:
    match = re.search(rf"^\s+{name}: \[(.*)\]$", block, re.M)
    return [v.strip().strip('"') for v in match.group(1).split(",")] if match else []


def test_the_required_jobs_are_exactly_those_ci_declares() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    text = workflow[workflow.index("\njobs:\n") :]
    declared = set()
    for match in re.finditer(r"^  ([a-z-]+):\n(.*?)(?=^  [a-z-]+:\n|\Z)", text, re.M | re.S):
        name, block = match.group(1), match.group(2)
        oses, pythons = matrix_values(block, "os"), matrix_values(block, "python")
        if oses and pythons:
            declared |= {f"{name} ({o}, {p})" for o in oses for p in pythons}
        elif oses or pythons:
            declared |= {f"{name} ({v})" for v in oses or pythons}
        else:
            declared.add(name)
    assert declared == ci_gate.REQUIRED_JOBS
    assert len(declared) == 30
