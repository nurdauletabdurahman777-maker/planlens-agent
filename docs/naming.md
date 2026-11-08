# Naming

**Status: provisional. Nothing has been registered or reserved.**

| what | provisional name | why |
| --- | --- | --- |
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

## Not checked

- Console-command collisions with other packages or system tools.
- GitHub organisation or repository name availability.
- Trademarks.

## Before publication

Decide the final names, then re-check availability immediately before
registering, and update `pyproject.toml`, the import package, the CLI entry
point, the JSON schema identifiers (`planlens-agent/...`) and this file
together. Schema identifiers are part of the data format, so renaming them
after release would need a migration note.
