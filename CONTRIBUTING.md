# Contributing

PlanLens is in its 0.x series, and its public API may evolve. You need Git and [uv](https://docs.astral.sh/uv/).

## Set up and test

<!-- run prereq -->
```sh
git clone https://github.com/nurdauletabdurahman777-maker/planlens-agent.git
```

Run everything below from the root of the clone. No virtual environment has to be active: `uv run` creates
`.venv` and uses it.

<!-- run dev contains=" passed" contains="Success: no issues found" -->
```sh
uv run --locked --extra dev pytest tests -q
uvx ruff@0.17.4 format --check .
uvx ruff@0.17.4 check .
uvx mypy@2.4.1 --strict src/planlens_agent
```

Every CI stage, as CI runs it:

```text
uv run --no-project --python 3.14 scripts/ci_local.py all --python 3.14
```

## What to keep true

- The core has no runtime dependencies. Development tools stay out of the wheel.
- The checker is deterministic and makes no model calls.
- Every command in a `sh` block in the README, Quickstart or this file must work as written. The `docs` CI stage
  runs them. A command that is meant to fail goes last in its block.
- Label authored examples as authored. Do not report results that were not observed.

## Regenerate media

On macOS with Google Chrome, from the root of the clone:

```text
uv run --script scripts/make_demo_media.py
```

This rebuilds `docs/media` and `docs/brand` from the real package. Its tools are used only by that script.
Add `--only NAME` to rebuild selected outputs, for example `--only capabilities`; `--help` lists the names.

## Report a bug

Open an issue, and include:

- the output of `planlens-agent --version`, your Python version and operating system;
- the exact command and its exit status;
- the smallest catalog, contract and plan that show the problem;
- what you expected, and the full output.

Remove credentials and anything confidential first. A wrong verdict is the most important kind of bug: include
the plan, and say which rule should or should not have applied.
