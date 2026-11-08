# Provenance

## This repository

- Created locally on 2026-11-06 as an independent Git repository named
  `planlens-agent`, alongside (not inside) the research checkout; branch `main`.
- It is not a branch, fork or worktree of the research artifact, and it has
  **no Git remote**. No GitHub repository has been created for it.
- No commits have been made yet; the work is uncommitted for review.

## Relationship to the research artifact

The product descends conceptually from the PlanLens research artifact:

| | |
| --- | --- |
| repository | https://github.com/nurdauletabdurahman777-maker/planlens-icdm2026 |
| commit consulted | `6ae48bd8073f951396e8329f8033bd14fc2e0cb4` (`main`, also tagged `v0.1.1-icdm2026-teen` and `v0.2.0-icdm2026-teen`) |
| license | Apache-2.0 for code; CC BY 4.0 for documentation, figures and derived data (per its `LICENSE.md`) |
| how it was used | read-only, through the GitHub API; no local clone was made, and nothing in it was modified |

### Imported files

| file here | source path at that commit | modification | sha256 |
| --- | --- | --- | --- |
| `LICENSE` | `LICENSE` | none (verbatim Apache License 2.0 text) | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |

### Third-party material

| file here | source | modification | sha256 |
| --- | --- | --- | --- |
| `tests/data/sarif/sarif-schema-2.1.0.json` | OASIS SARIF 2.1.0 Errata 01 schema, https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json, retrieved 2026-11-07 | none | `c3b4bb2d6093897483348925aaa73af03b3e3f4bd4ca38cef26dcb4212a2682e` |

Test-only, and not in the wheel; it is in the sdist because the tests are. It is under
the OASIS notice reproduced in `tests/data/sarif/NOTICE.md`, not Apache-2.0. Project
decision of 2026-11-07: retained unmodified as a separately licensed test resource,
with its source, hash and full notice.

### Not imported

**No source code, tests, data, figures or documentation were copied.** The
research evaluator diagnoses dependency-graph plans with a twenty-category
taxonomy; this product checks owner contracts with a different model
(catalog actions, prerequisites, planned artifact identity). The ideas carried
over - a deterministic, non-LLM judge; failures localised to plan positions;
recorded expected outcomes - were reimplemented from scratch.

Because no research code was adapted, the Standard's requirement to compare
adapted behaviour against the pinned original evaluator does not currently
apply. If research code is adapted later, record it here with its source path,
commit and modifications, and add that comparison.

### Research evidence

Research results (ICDM, IRCYS conformance work) are separate and are not
evidence for this product. Nothing in this repository reports adoption,
productivity, accuracy on real agent plans, or production safety.

## Tooling used to build and test

Exact versions used for the checkpoint A verification:

| tool | version | pinned by `uv.lock`? |
| --- | --- | --- |
| CPython | 3.14.7 and 3.11.15 (macOS 26.6.2, arm64), uv-managed | no |
| uv | 0.12.3 | no |
| hatchling (build backend) | 1.33.0, as recorded in the wheel's `WHEEL` file | **no**, but pinned in `pyproject.toml` `[build-system]` (see below) |
| pytest, jsonschema and their dependencies | see `uv.lock` (13 packages) | **yes** - the `dev` extra |
| Playwright (browser checks only; never a dependency of this project) | 1.64.1, in a separate tools environment | **no** |
| Google Chrome (browser checks) | 156.0.8078.80 | **no** |
| ruff | 0.17.4, run with `uvx` | **no** |
| mypy | 2.4.1, run with `uvx` | **no** |
| actionlint | `actionlint-py` 1.7.12.25, run with `uvx` (workflow checks only) | **no**, pinned in `scripts/ci_local.py` |
| LangGraph (recipe only; never a dependency of this project) | 1.2.20 | **no**, pinned with hashes in `examples/langgraph_gate/requirements.lock` |

`uv.lock` is the reproduction lock for the test dependencies and ships in the
sdist. It does not cover the build backend or the lint and type-check tools.

The build backend was unpinned until the second source review, when two builds
of the same source an hour apart used hatchling 1.32.0 and then 1.33.0 - all of
1.31.0, 1.32.0 and 1.33.0 were in the local cache, and an unpinned offline build
takes whichever is newest. `[build-system]` now pins `hatchling==1.33.0`. Ruff
and mypy remain unpinned by the project; the README runs the exact versions
above with `uvx`.

## Build reproducibility

Claims here stay limited to what was observed.

- Repeated builds in one environment gave identical wheel and sdist bytes.
- The independent review of checkpoint B1 rebuilt on Linux and reproduced the exact wheel. Its
  sdist differed: 162,447 bytes, SHA-256
  `9c0a755c0c9c7afd31ed746fd7c379d1b07675d3990b6a3211a5948b31ab0fc6`, against the macOS
  sdist `b3f9ee3d...`.

So the wheel has matched across those two environments, and the sdist has not. The cause of
the sdist difference has not been established. No universal cross-environment byte
reproducibility is claimed for either.
