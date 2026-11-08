# Changelog

## 0.1.0.dev0 - unreleased (checkpoint B2)

Added

- `suite run` and `suite diff`: run developer-supplied candidates through `check()`, save
  an auditable run (`planlens-agent/run/v1`), and compare two runs
  (`planlens-agent/run-diff/v1`). See `docs/suites.md`.
- Runs record plan content and digests, contract and catalog identity, report and rule
  versions, and an evaluator digest that tells apart builds sharing a version string.
- `load_suite`, `run_suite`, `load_run`, `compare_runs` in the SDK; schemas for suites, runs
  and comparisons.
- `candidates/` and `suite.toml` in the `release-basic` template, all authored.
- `examples/langgraph_gate`: a planner, check, execute-or-reject graph, tested with
  LangGraph 1.2.20. The core still has no runtime dependencies.
- `.github/workflows/ci.yml` and `consumer-demo.yml`, prepared and not yet run on a hosted
  service; `scripts/ci_local.py` runs the same stages locally; `scripts/journey.py` walks the
  whole journey against an installed command.

Fixed

- A file name too long for the operating system made `check()` raise, and the CLI report an
  internal error, for plans, contracts and catalogs. It is now an `unreadable` finding, with
  no source location invented. The same fault is fixed in `--source-root`, `init` and
  `regress`.

Fixed after independent review of B2 (B2-r2)

- LangGraph recipe: a returned state or caller-supplied fields could authorize a later
  invocation whose planner failed, and removing the plan after the check skipped the
  comparison. The graph now accepts only `request`, every invocation starts clean, and
  execution needs a current plan.
- `load_run`: every declared type is checked before use, so a malformed value is a
  `RunFileError` instead of an uncaught `TypeError`. Booleans and floats are no longer
  accepted as integers, identifiers cannot end in a newline, and findings must agree with the
  run's rule versions.
- `suite diff`: two runs agreeing on an unsupported `report_schema` are `not_comparable`.
- `suite diff --output` refuses to replace either input, including through other spellings,
  symlinks and hard links.

Fixed after independent review of B2-r2 (B2-r3)

- `check`, `inspect` and `suite run --overwrite` could overwrite their own inputs. All of them
  now refuse, with exit 2, an output that is the plan, contract, catalog, suite manifest or a
  candidate in use, including through other spellings, symlinks and hard links.

Unchanged

- `check` and `inspect` output: byte-identical to the previous checkpoint's wheel across the
  acceptance cases and bundled plans.

## 0.1.0.dev0 - unreleased (checkpoint A)

Not published. Distribution, module and command names are provisional
(see `docs/naming.md`).

Added

- Owner-supplied action catalog and contract (`.toml` or `.json`), validated
  together before any plan is read: schema, catalog pin, unknown actions,
  prerequisite cycles, inconsistent or unsatisfiable artifact rules, and
  unsupported rule kinds.
- Ordered sequential plans with unique step ids and `"<step>.<output>"`
  artifact references.
- Checks: unknown actions, required actions, prerequisites, reference
  resolution and order, input binding and type, planned artifact identity, and
  attempts by a plan to override policy.
- One diagnostic model for the SDK, CLI and JSON reports
  (`planlens-agent/report/v1`), with stable rule ids and offline explanations.
- CLI `planlens-agent` with `check` (exit 0 / 1 / 2), `validate-policy`,
  `regress`, `init`, `rules`, `explain`.
- `ApprovedPlan` snapshots and a `ReferenceRunner` that dispatches only the
  exact approved snapshot.
- `release-basic` template, regression fixtures, JSON Schemas.

Fixed during the checkpoint A source review (see `A_REVIEW.md`)

- An integer longer than 4,300 digits in a JSON or TOML document made `check()`
  raise `ValueError`; through the CLI it was reported as an internal error with
  no report. It is now a `<doc>/limit` finding.
- Excessive nesting was reported as `malformed` or `schema` depending on which
  depth check fired first. It is now always `<doc>/limit`.

Fixed after the second source review (see `A_REVIEW.md`, round 2)

- A validated policy exposed mutable dicts, so clearing an action's inputs made a
  rejected plan pass under the same digest. Catalog and contract are now
  read-only private copies bound to their content; `check(policy=...)` and the
  runner refuse a policy that no longer matches (`PolicyIntegrityError`).
- The runner re-checked the plan, then dispatched from `approval.plan`, which a
  caller could mutate during dispatch. It now dispatches its own validated
  read-only copy, and `ApprovedPlan` always holds a private copy.
- A JSON escape for an unpaired surrogate (`"\ud800"`) crashed checking of plans,
  contracts and catalogs. It is now a `<doc>/schema` finding with a location.
- `contract/unsatisfiable-input` (rule version 2) now detects required actions
  whose inputs cannot be bootstrapped, not only types no action outputs.
- `uv.lock` is included in the sdist.

Added in checkpoint B1 (see `B1_REVIEW.md`)

- `planlens-agent inspect PLAN --output FILE`: a self-contained HTML report of one
  check, built from an explicit view model (`build_view`, `render_html`).
- `planlens-agent check --format sarif [--source-root DIR]`: SARIF 2.1.0 export
  (`to_sarif`, `to_sarif_json`). `--output` saves what is printed in this mode;
  text and JSON modes are unchanged.
- `Policy.catalog_origin` and `Policy.contract_origin`, so a syntax error in a policy
  document can be located in its file.

Fixed after the B1 source review (see `B1_REVIEW.md`, section 7)

- More than 702 distinct artifact references in one report made `build_view` raise
  `IndexError`, so `inspect` gave no report. Group labels now continue past ZZ with no
  limit, and a label stands for one reference across all rules.
- SARIF export resolved relative paths against the working directory at export time.
  Locations are now fixed when each document is read (`CheckResult.plan_source`,
  `Policy.contract_source`, `Policy.catalog_source`).
- The diagram's relationship limit is now checked before any of it is built.
- Raw observed and required data moved behind native disclosure controls, with a compact
  summary from the same structured fields left visible.
