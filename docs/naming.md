# Naming

**Status: selected by the owner. PyPI registration is pending: nothing has been registered or reserved.**

| what | selected name | why |
| --- | --- | --- |
| product | PlanLens | the name people use for the tool |
| repository | `planlens-agent` | matches the distribution |
| distribution | `planlens-agent` | `planlens` on PyPI is an unrelated project |
| import package | `planlens_agent` | distinct from the research artifact's `planlens_v2` |
| console command | `planlens-agent` | the research artifact already installs a `planlens` command |

## Findings, 2026-11-06

- **PyPI `planlens`** exists and belongs to an unrelated project ("Review-ready
  data from AEC documents for LLMs", version 0.14.0 when checked at
  2026-11-06T17:11:12Z). It must not be advertised as this package.
- **PyPI `planlens-agent`, `planlens_agent`, `planlensagent`** returned HTTP 404
  from `https://pypi.org/pypi/<name>/json` at the same time. A 404 means no
  project page existed then. It does **not** show the name can be registered:
  PyPI may refuse names it considers too similar to existing projects, and
  someone else may register it first.
- **The research artifact** (`planlens-icdm2026`, `pyproject.toml` at commit
  `6ae48bd8073f951396e8329f8033bd14fc2e0cb4`) declares distribution `planlens`,
  module `planlens_v2` and console script `planlens = "planlens_v2.cli:main"`.
  A product command named `planlens` would overwrite the research command when
  both are installed in one environment, so this product uses a different one.

## Finding, 2026-11-10

- **PyPI `planlens-agent`** returned 404 in a lookup on November 10. This does not reserve or
  guarantee the name.

## Not checked

- Console-command collisions with other packages or system tools.
- GitHub organisation or repository name availability.
- Trademarks.

## Before publication

Re-check availability immediately before the first upload. If the name cannot be used, stop: a
different name means changing `pyproject.toml`, the import package, the CLI entry point, the JSON
schema identifiers (`planlens-agent/...`), the documentation and this file together, and reviewing
that change. Schema identifiers are part of the data format, so renaming them after release would
need a migration note.
