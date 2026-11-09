"""Decide from GitHub's Actions API whether CI passed for one exact release commit.

    python ci_gate.py --commit FULL_SHA

Needs GITHUB_TOKEN and GITHUB_REPOSITORY. Exits 0 only when CI is proven to have passed. Anything
else exits 1, including a response that cannot be read or does not add up.

A run counts as evidence only if it is this repository's own run of the CI workflow for this exact
commit, on a branch or tag of this repository. Runs of other workflows or commits, and runs from
forks, are ignored: they can neither satisfy this gate nor block it.

For the runs that count:

- A push to main must exist.
- Any run that has not completed blocks, even an older one that is being re-run.
- Runs are grouped by event and ref, and the newest run of each group decides that group. An older
  success cannot hide a newer failure. A newer success does supersede an older failure.
- A run reports its latest attempt, so a re-run that fails or is still going blocks even if an
  earlier attempt passed. Jobs are read for exactly that attempt.
- Every declared job must appear exactly once, completed and successful, and no other job may
  appear. "skipped" and "neutral" are not success.
- The runs are read again after the jobs. If anything changed in between, the result is refused.

Standard library only, in one file: the publish job runs its own copy of it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

API = "https://api.github.com"
REPOSITORY = "nurdauletabdurahman777-maker/planlens-agent"
WORKFLOW_FILE = "ci.yml"
WORKFLOW_PATH = ".github/workflows/ci.yml"
TRUSTED_EVENT, TRUSTED_BRANCH = "push", "main"
COMMIT = re.compile(r"[0-9a-f]{40}")
LOOPBACK = re.compile(r"http://127\.0\.0\.1:\d+")
# GitHub documents a limit of 1,000 results for a search that uses head_sha.
RESULT_LIMIT = 1000
PER_PAGE = 100
MAX_PAGES = 1 + RESULT_LIMIT
TIMEOUT_SECONDS = 30

ALL_OS = ("ubuntu-latest", "macos-latest", "windows-latest")
REQUIRED_JOBS = frozenset(
    ["lint"]
    + [f"test ({o}, {p})" for o in ALL_OS for p in ("3.11", "3.12", "3.13", "3.14")]
    + [f"package ({p})" for p in ("3.11", "3.14")]
    + [f"journey ({o})" for o in ALL_OS]
    + [f"recipe ({p})" for p in ("3.11", "3.14")]
    + [f"pilot ({o}, {p})" for o in ALL_OS for p in ("3.11", "3.14")]
    + [f"docs ({o}, {p})" for o in ("ubuntu-latest", "macos-latest") for p in ("3.11", "3.14")]
)

Get = Callable[[str, dict[str, Any]], dict[str, Any]]


class Blocked(Exception):
    """CI is not proven to have passed."""


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        raise Blocked("the API answered with a redirect, which this gate does not follow")


def api_base(value: str | None) -> str:
    """The token is only ever sent to GitHub, or to a loopback address in tests."""
    base = value or API
    if base != API and not LOOPBACK.fullmatch(base):
        raise Blocked(f"refusing to send the token to {base}")
    return base


def client(base: str, token: str) -> Get:
    opener = urllib.request.build_opener(_NoRedirects)

    def get(path: str, params: dict[str, Any]) -> dict[str, Any]:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        request = urllib.request.Request(
            f"{base}{path}{query}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "planlens-agent-ci-gate",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                body = json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise Blocked(f"the API answered {error.code} for {path}") from None
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            raise Blocked(f"no usable answer for {path}: {type(error).__name__}") from None
        if not isinstance(body, dict):
            raise Blocked(f"the answer for {path} is not an object")
        return body

    return get


def field(record: dict[str, Any], name: str, kind: type | tuple[type, ...]) -> Any:
    value = record.get(name)
    # bool is an int in Python, and no field read here is a flag.
    if not isinstance(value, kind) or isinstance(value, bool):
        raise Blocked(f"field {name!r} is missing or has the wrong type")
    return value


def full_name(run: dict[str, Any], name: str) -> str | None:
    """A repository's full name. A deleted head repository is reported as null."""
    value = run.get(name, "absent")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise Blocked(f"field {name!r} is missing or has the wrong type")
    return str(field(value, "full_name", str))


def paged(get: Get, path: str, key: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Every item of a listing, or Blocked if the pages do not add up to its total_count."""
    items: list[dict[str, Any]] = []
    total: int | None = None
    for page in range(1, MAX_PAGES + 1):
        body = get(path, {**params, "per_page": PER_PAGE, "page": page})
        count = field(body, "total_count", int)
        if total not in (None, count):
            raise Blocked(f"{path} changed its total_count while being read")
        total = count
        if count > RESULT_LIMIT:
            raise Blocked(f"{path} has {count} results, more than the API can list reliably")
        batch = field(body, key, list)
        if not all(isinstance(item, dict) for item in batch):
            raise Blocked(f"{path} returned an item that is not an object")
        items += batch
        if len(items) >= count:
            break
        if not batch:
            raise Blocked(f"{path} stopped at {len(items)} of {count} results")
    else:
        raise Blocked(f"{path} did not finish within {MAX_PAGES} pages")
    ids = [field(item, "id", int) for item in items]
    if len(items) != total or len(set(ids)) != len(ids):
        raise Blocked(f"{path} returned {len(items)} items for a total of {total}, or repeats")
    return items


def workflow_id(get: Get, repository: str) -> int:
    workflow = get(f"/repos/{repository}/actions/workflows/{WORKFLOW_FILE}", {})
    if field(workflow, "path", str) != WORKFLOW_PATH:
        raise Blocked(f"the workflow is at {workflow.get('path')}, not {WORKFLOW_PATH}")
    if field(workflow, "state", str) != "active":
        raise Blocked(f"the CI workflow is {workflow.get('state')}")
    return int(field(workflow, "id", int))


def fetch_runs(
    get: Get, repository: str, workflow: int, commit: str
) -> tuple[list[dict[str, Any]], int]:
    """The runs that count as evidence, and how many returned runs were ignored."""
    runs = paged(
        get,
        f"/repos/{repository}/actions/workflows/{workflow}/runs",
        "workflow_runs",
        {"head_sha": commit, "exclude_pull_requests": "true"},
    )
    counted, ignored = [], 0
    for run in runs:
        if full_name(run, "repository") != repository:
            raise Blocked(f"run {run.get('id')} belongs to another repository")
        same_workflow = (
            field(run, "workflow_id", int) == workflow
            and field(run, "path", str).partition("@")[0] == WORKFLOW_PATH
        )
        same_commit = field(run, "head_sha", str) == commit
        if same_workflow and same_commit and full_name(run, "head_repository") == repository:
            for name in ("run_number", "run_attempt"):
                field(run, name, int)
            field(run, "event", str)
            field(run, "status", str)
            counted.append(run)
        else:
            ignored += 1
    return counted, ignored


def describe(run: dict[str, Any]) -> str:
    return (
        f"run {run['id']} #{run['run_number']} attempt {run['run_attempt']} "
        f"({run['event']} on {run.get('head_branch')}): "
        f"{run['status']}/{run.get('conclusion')}"
    )


def newest_per_group(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    newest: dict[tuple[str, str | None], dict[str, Any]] = {}
    for run in sorted(runs, key=lambda r: (r["run_number"], r["id"])):
        newest[(run["event"], run.get("head_branch"))] = run
    return list(newest.values())


def run_problems(runs: list[dict[str, Any]]) -> list[str]:
    """Why these runs do not show success, without looking at their jobs."""
    found = [f"not completed: {describe(r)}" for r in runs if r["status"] != "completed"]
    found += [
        f"newest run of its event and ref did not succeed: {describe(r)}"
        for r in newest_per_group(runs)
        if r["status"] == "completed" and r.get("conclusion") != "success"
    ]
    if not any(
        r["event"] == TRUSTED_EVENT and r.get("head_branch") == TRUSTED_BRANCH for r in runs
    ):
        found.append(f"no CI run from a {TRUSTED_EVENT} to {TRUSTED_BRANCH} for this commit")
    return found


def job_problems(jobs: list[dict[str, Any]], run: dict[str, Any], commit: str) -> list[str]:
    """Why the jobs of the run's latest attempt do not show success."""
    found, seen = [], set()
    for job in jobs:
        name = field(job, "name", str)
        if name in seen:
            found.append(f"job appears twice: {name}")
        seen.add(name)
        if name not in REQUIRED_JOBS:
            found.append(f"job is not declared by this gate: {name}")
        if field(job, "run_id", int) != run["id"]:
            found.append(f"job belongs to another run: {name}")
        if field(job, "run_attempt", int) != run["run_attempt"]:
            found.append(f"job belongs to another attempt: {name}")
        if field(job, "head_sha", str) != commit:
            found.append(f"job is for another commit: {name}")
        status, conclusion = field(job, "status", str), job.get("conclusion")
        if status != "completed" or conclusion != "success":
            found.append(f"job did not succeed: {name}: {status}/{conclusion}")
    found += [f"required job is missing: {name}" for name in sorted(REQUIRED_JOBS - seen)]
    return found


def snapshot(runs: list[dict[str, Any]]) -> set[tuple[Any, ...]]:
    return {(r["id"], r["run_attempt"], r["status"], r.get("conclusion")) for r in runs}


def evaluate(get: Get, repository: str, commit: str) -> list[str]:
    """Raises Blocked, or returns a report of what was verified."""
    workflow = workflow_id(get, repository)
    runs, ignored = fetch_runs(get, repository, workflow, commit)
    problems = run_problems(runs)
    if problems:
        raise Blocked("; ".join(problems))
    decisive = newest_per_group(runs)
    for run in decisive:
        path = f"/repos/{repository}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs"
        jobs = paged(get, path, "jobs", {})
        problems = job_problems(jobs, run, commit)
        if problems:
            raise Blocked(f"{describe(run)}: " + "; ".join(problems))
    again, _ = fetch_runs(get, repository, workflow, commit)
    if snapshot(again) != snapshot(runs):
        raise Blocked("CI changed while it was being checked; check again")
    report = [f"verified: {describe(run)}, {len(REQUIRED_JOBS)} jobs" for run in decisive]
    superseded = len(runs) - len(decisive)
    report.append(f"superseded older runs: {superseded}; ignored runs: {ignored}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    commit = parser.parse_args().commit
    try:
        repository = os.environ.get("GITHUB_REPOSITORY")
        if repository != REPOSITORY:
            raise Blocked(f"this gate is for {REPOSITORY}, not {repository}")
        if not COMMIT.fullmatch(commit):
            raise Blocked("--commit must be a full lowercase commit SHA")
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise Blocked("GITHUB_TOKEN is not set")
        get = client(api_base(os.environ.get("GITHUB_API_URL")), token)
        report = evaluate(get, repository, commit)
    except Blocked as reason:
        print(f"BLOCKED: {reason}")
        return 1
    except Exception as error:  # noqa: BLE001 - an unexpected failure must never let a release through
        print(f"BLOCKED: unverifiable: {type(error).__name__}")
        return 1
    print(f"CI passed for {commit}")
    print("\n".join(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
