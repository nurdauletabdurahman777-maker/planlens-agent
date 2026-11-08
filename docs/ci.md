# CI

**Nothing here has run on a hosted service.** No remote exists. The workflows are prepared
and statically checked; the commands below were run locally. Hosted CI and SARIF ingestion
stay unverified until they run.

## Local commands

`scripts/ci_local.py` is what `.github/workflows/ci.yml` calls, so the stages are the same
code in both places. It needs `uv`.

```console
python scripts/ci_local.py lint                  # ruff format and check, mypy --strict, actionlint
python scripts/ci_local.py test --python 3.11    # pytest from uv.lock
python scripts/ci_local.py package --python 3.11 # build, inspect the wheel, test the installed wheel
python scripts/ci_local.py journey --python 3.14 # scripts/journey.py against the installed command
python scripts/ci_local.py recipe --python 3.11  # hashed LangGraph lock, recipe tests, full journey
python scripts/ci_local.py all --python 3.14
```

Environments and build output go to a temporary directory. A failing command stops the stage
with its exit status. `ruff format` also covers Python blocks in Markdown.

## Verification status

| stage | local (macOS 26.6.2 arm64) | hosted |
| --- | --- | --- |
| lint | run | pending: Ubuntu |
| test | run: CPython 3.11.15, 3.14.7 | pending: Ubuntu, macOS, Windows on 3.11 to 3.14 |
| package | run: 3.11, 3.14 | pending: Ubuntu on 3.11, 3.14 |
| journey | run: 3.14 | pending: Ubuntu, macOS, Windows on 3.14 |
| recipe | run: 3.11, 3.14, LangGraph 1.2.20 | pending: Ubuntu on 3.11, 3.14 |
| consumer demo | commands run by hand, exit statuses matched | pending |
| SARIF upload | SARIF generated; upload not attempted | pending |

## Workflow safety

Enforced by `tests/test_ci_files.py`:

- `permissions: contents: read` by default; `security-events: write` on the SARIF job alone;
- actions pinned to full commits; checkouts do not keep credentials;
- no `pull_request_target`, `continue-on-error`, `|| true`, secrets or provider credentials;
- diagnostic uploads use `if: always()`, so a failing gate keeps its evidence and stays failed;
- no workflow commits, pushes or overwrites a baseline.

Action input names were read from each action's `action.yml` at the pinned commit.

## Consumer demo

`consumer-demo.yml` starts only by hand. **`reject-demo` and `sarif-demo` fail on purpose**, so
a red run is the expected result:

| job | gate | expected |
| --- | --- | --- |
| `pass-demo` | `suite run` on `candidates/v3` | passes |
| `reject-demo` | `suite run` on `candidates/v2`, then `suite diff` | fails, artifacts kept |
| `sarif-demo` | `check --format sarif` on a rejected plan, then upload | fails, upload attempted |

Its candidates are authored, and its baseline is generated in the job so the demo stands
alone. Do not copy that part.

## Baselines

A baseline is an **owner-reviewed input**.

- Store it in the repository, for example `.planlens/baseline.run.json`, and require owner
  review with CODEOWNERS.
- Read it from the base branch, so a pull request cannot change the baseline it is judged by.
- Never write candidate or pull-request output over it automatically.
- Pin `planlens-agent`. A new evaluator build makes comparisons `not_comparable` (exit 2) by
  design: regenerate the baseline deliberately, and review that change.

A consumer job, adapted to your project. Candidates come from your own step, and running a
real planner in CI needs your own decision about credentials, which should never be exposed to
forks:

```yaml
on:
  pull_request:

permissions:
  contents: read

jobs:
  plans:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0
          persist-credentials: false
      # install your pinned planlens-agent, then write candidates to ./candidates
      - name: Read the reviewed baseline from the base branch
        env:
          BASE_REF: ${{ github.base_ref }}
        run: git show "origin/$BASE_REF:.planlens/baseline.run.json" > baseline.run.json
      - name: Gate
        run: planlens-agent suite run suite.toml --plans-dir candidates --output candidate.run.json
      - name: Show what changed
        if: always()
        run: planlens-agent suite diff baseline.run.json candidate.run.json
      - uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2
        if: always()
        with:
          name: planlens-run
          path: "*.run.json"
```

This template has not been run. Gate on `suite run`: `suite diff` can exit 0 while the
candidate is still rejected.
