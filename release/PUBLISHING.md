# Publishing PlanLens 0.1.0

How the first public release goes from a reviewed local candidate to PyPI and GitHub. Local preparation
is complete when the review bundle exists.

**The hold is absolute until the owner gives explicit authorization.** Nothing in "Before the launch" or
"Launch" happens without it. One explicit owner authorization may cover a concrete, reviewed launch
sequence, so long as it names the commit and the steps it covers; the mechanical steps inside it do not
each need separate conversational approval. An authorization does not extend to a different commit or to
steps it does not name.

No authorization overrides the stop conditions below, the CI gate in `publish.yml` or the review required
by the `pypi` GitHub environment. If one of those stops the sequence, the authorization ends there.

| Item | Value |
| --- | --- |
| Product | PlanLens |
| Repository, distribution, CLI | `planlens-agent` |
| Python module | `planlens_agent` |
| Version and tag | `0.1.0`, `v0.1.0` |
| Release title | PlanLens v0.1.0 — Lint AI-agent plans before they run |
| Publishing method | PyPI Trusted Publishing through GitHub OIDC |
| Workflow, environment | `.github/workflows/publish.yml`, `pypi` |

In the commands below, `REPO` is `nurdauletabdurahman777-maker/planlens-agent` and `COMMIT` is the full
SHA of the approved commit.

## Local preparation

No external state changes here.

- Version, metadata, lock file, documentation and current demonstration output are at 0.1.0.
- `scripts/ci_local.py` stages pass locally, and `scripts/release_check.py` passes on a built pair.
- `dist/planlens-release-0.1.0-review.zip` holds the candidate source, the diff from the reviewed
  baseline, these documents, a wheel and sdist, logs, hashes and a manifest.

The documented `pip install` commands have been verified against a local wheel with pip confined to it.
That does not show the package can be installed from PyPI. Step 8 does.

## Before the launch

These change external accounts, so the owner's authorization must cover them. Do them after step 1 and
before step 7.

- **PyPI.** Re-check that `planlens-agent` is still available: an earlier 404 reserves nothing. Add a
  pending Trusted Publisher with owner `nurdauletabdurahman777-maker`, repository `planlens-agent`,
  workflow `publish.yml` and environment `pypi`. A pending publisher does not reserve the name either.
- **GitHub environment.** Create the `pypi` environment, require the owner's review, and restrict
  deployments to tags matching `v*`.
- **Actions.** Confirm that hosted workflows can run for the repository.

## Launch

Each step depends on the one before it. If a stop condition applies, stop there and do not continue.

### 1. Review the final candidate

Review the bundle and what visibility will expose. Flag concerns without deleting or rewriting history;
what to do about each one is an owner decision.

- Every pushed branch, including development and pilot branches: `git ls-remote --heads origin`.
- Every commit's author name, email address and trailers:
  `git log --all --format='%h %an <%ae> %(trailers:only,unfold)'`.
- Files that become public on merge, such as `pilot/`: `git ls-tree -r --name-only COMMIT`.
- Actions run history, logs and retained artifacts from the private period:
  `gh run list -R REPO --limit 200` and `gh api repos/REPO/actions/artifacts`.
- Issues, pull requests and repository settings.

### 2. Make the repository public

Only under the owner's authorization:

```text
gh repo edit REPO --visibility public --accept-visibility-change-consequences
```

### 3. Push the candidate and obtain green hosted CI

```text
git push -u origin release/prepare-0.1.0
```

Wait for `CI` on `COMMIT` to finish. Every job must succeed on the public repository.

Stop if a job fails. A fix makes a new candidate: review it again from step 1.

### 4. Merge into main

Only after those checks pass. A fast-forward keeps the reviewed commit as `main`:

```text
git switch main
git merge --ff-only COMMIT
git push origin main
```

Stop if the fast-forward is refused, because `main` then holds work that was not reviewed.

Pushing `main` starts a separate `CI` run for the same commit. That run is the one publishing depends on.

### 5. Verify the exact main commit

```text
git fetch origin
git rev-parse origin/main
gh run list -R REPO --commit COMMIT --workflow ci.yml --json status,conclusion,headSha
```

`origin/main` must equal `COMMIT`, and every `CI` run listed for it must be `completed` with conclusion
`success`, including the run from the push to `main`.

### 6. Create the tag

```text
git tag -a v0.1.0 -m "PlanLens v0.1.0" COMMIT
git push origin v0.1.0
```

### 7. Publish to PyPI

Pushing the tag starts another `CI` run for the same commit. Wait for it to finish. Then start the
workflow from the tag. Nothing else starts it:

```text
gh workflow run publish.yml -R REPO --ref v0.1.0 -f version=0.1.0 -f commit=COMMIT
```

The `check` job first runs `scripts/ci_gate.py`, which stops the workflow unless GitHub's Actions API
shows that `CI` passed for exactly this commit: a successful push to `main`, all 30 declared jobs on the
latest attempt, and nothing failed or unfinished on any other ref of the commit. It then builds the wheel
and sdist once, runs `scripts/release_check.py` and `twine check` on them, and records their hashes in the
job summary. The `publish` job waits for approval of the `pypi` environment, downloads exactly those two
files, verifies them against `SHA256SUMS`, runs the CI gate again and uploads them.
**Do not rebuild between checking and uploading**, and do not upload by any other route.

Before approving, compare the job summary with the hashes in the reviewed bundle. If they differ, stop
and compare the contents of the two pairs. Approve only when the difference is explained.

The publishing action also generates attestations by default. They have not been exercised.

PyPI does not allow a filename to be reused, even after deletion. If an upload fails partway, do not
retry with a rebuilt pair. Find out what PyPI holds first. A corrected release needs a new version and a
new review.

### 8. Verify installation from PyPI

In a new, empty folder, with no local wheel available:

```text
python3 -m venv verify-env
. verify-env/bin/activate
python -m pip install --no-cache-dir "planlens-agent==0.1.0"
planlens-agent --version
planlens-agent init release-basic demo
cd demo
planlens-agent check plans/artifact-mismatch.json
```

Expect `planlens-agent 0.1.0`, then `RESULT: REJECTED (exit 1)` with `plan/artifact-identity`.

Compare what PyPI serves with the checked pair: the `digests.sha256` values at
`https://pypi.org/pypi/planlens-agent/0.1.0/json` must equal `SHA256SUMS`. Open the project page and check
that the description, image and every link work.

### 9. Publish the GitHub Release

Use the files from the workflow run that published. Do not build them again:

```text
gh run download RUN_ID -R REPO -n release-pair -D release-pair
cd release-pair/dist
shasum -a 256 -c ../SHA256SUMS
cd ../..
gh release create v0.1.0 -R REPO --verify-tag \
  --title "PlanLens v0.1.0 — Lint AI-agent plans before they run" \
  --notes-file release/NOTES_0.1.0.md \
  release-pair/dist/* release-pair/SHA256SUMS
```

### 10. Prepare the announcement

Only after the public links and installation work. Keep it to what has been observed.

After the release, update `docs/naming.md` and `docs/evidence.md` to record what was registered and
verified, in an ordinary reviewed change. Those edits come after the tag and are not part of it.

## Stop conditions

Stop, and leave what exists in place, if any of these is true.

| Check | Stop if |
| --- | --- |
| Version | `pyproject.toml`, `_version.py`, `uv.lock`, the wheel, the sdist or `--version` do not all say `0.1.0` |
| Tag | The tag is not `v0.1.0`, does not point at `COMMIT`, or already exists on another commit |
| Commit | `origin/main` is not `COMMIT`, or the commit differs from the reviewed candidate |
| Artifacts | Any file differs from `SHA256SUMS`, there are not exactly two distributions, or the hosted and reviewed hashes differ without explanation |
| CI | `scripts/ci_gate.py` refuses, for any reason, or any `CI` job on `COMMIT` is not `success`. Do not work around the gate; fix the cause, or re-run CI, and start again |
| Name | `planlens-agent` is no longer available to this project on PyPI |
| PyPI | The installed version, example output or served hashes differ from those expected |
