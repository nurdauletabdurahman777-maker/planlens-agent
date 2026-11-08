# SARIF export

```console
planlens-agent check plan.json --format sarif
planlens-agent check plan.json --format sarif --output plan.sarif --source-root .
```

Output is SARIF 2.1.0. Exit codes stay 0, 1 and 2. With `--format sarif`,
`--output` saves exactly what is printed. With `--format text` or `json`, `--output`
still saves the JSON report, as before. From Python: `to_sarif(result, source_root=...)`
and `to_sarif_json(...)`.

## Mapping

Built from the same diagnostics and rule registry as the JSON report.

| finding category | SARIF | `executionSuccessful` |
| --- | --- | --- |
| `violation` | result, `kind: fail`, `level: error` | true, if nothing else stopped the analysis |
| `invalid_plan` | result, `kind: fail`, `level: error` | false |
| `unsupported` | result, `kind: open`, `level: none` | false |
| `invalid_contract`, `invalid_catalog` | `toolConfigurationNotifications`, `level: error`; no result claims anything about the plan | false |

So a completed analysis that rejects a plan has `executionSuccessful: true` with
exit code 1, and an analysis that could not evaluate its required checks has
`executionSuccessful: false` with exit code 2. `kind: open` is SARIF's "could not
determine", which is what an unsupported required rule is. It is not a pass.
`invocations[0].properties` repeats the verdict, error kind and whether the plan was
evaluated.

Rules are in `tool.driver.rules`; invalid-policy rules are in
`tool.driver.notifications`, because SARIF indexes those arrays separately. Each
carries its rule id, rule version, description and offline help. The owner's rule id
is in `result.properties.contract_rule`, with `observed`, `required`, `suggestion`
and `evidence` beside it.

`tool.driver.version` is the engine version. `semanticVersion` is omitted because
the version is PEP 440 (`0.1.0.dev0`), which is not SemVer.

## Locations

| source | physical location | logical location |
| --- | --- | --- |
| file inside `--source-root` | relative, URI-encoded, `uriBaseId: SRCROOT` | yes |
| file outside `--source-root` | absolute `file:` URI | yes |
| SDK mapping | none | yes |
| a required step that does not exist | none; `properties.location_absent_reason` says why | none; the contract requirement is a related location |

- A document's location is fixed when it is read, as an absolute path held on the
  result (`plan_source`) and the policy (`contract_source`, `catalog_source`). Exporting
  later, from any directory, gives the same locations, and export never touches the
  filesystem. `to_sarif(source_root=...)` is itself resolved when you call it, so pass an
  absolute path if the working directory may have changed.
- **A location says where the document was read, not that it is unchanged.** If the file
  is edited or deleted after the check, the log still points at it. The
  `canonical_sha256` values identify what was checked; they are digests of the parsed
  content and were not recomputed from the file at export.
- `--source-root` defaults to the current directory. It must exist.
- Every location keeps its logical path, such as `plan:/steps/5/inputs/artifact`, with
  the step id and `step_index` beside it.
- A `region` (line, column) appears only when a parser reported one, which means JSON
  and TOML syntax errors. TOML positions need Python 3.14. Plan positions are never
  turned into line numbers, so most findings have a file and no line.
- `originalUriBaseIds.SRCROOT` deliberately has no absolute `uri`, so the log does not
  disclose the local path. A consumer maps `SRCROOT` to its own checkout root.
- A file outside the source root does put an absolute path in the log, and a
  "file not found" message quotes the path it was given. Set `--source-root` and use
  relative paths for a log you intend to share.

## What has and has not been verified

- **Verified:** logs validate against the official OASIS SARIF 2.1.0 schema. An
  unmodified copy is in `tests/data/sarif/`, used by tests only and never fetched; its
  source, hash and licence notice are in `NOTICE.md` there.
- **Attempted, refused:** the `sarif-demo` job in `.github/workflows/consumer-demo.yml` passes
  the upload action's validation, then GitHub answers `Code scanning is not enabled for this
  repository` on the private repository used so far. Nothing has been processed or displayed.
  See [ci.md](ci.md).
- **Not verified:** ingestion by GitHub code scanning or any other service, and
  pull-request annotations. Schema validity does not show that a service accepts a
  log or displays it usefully. Services commonly expect a file and line for each
  result, so findings without a physical location or region may be dropped or shown
  at the top of a file. Hosted ingestion is outside this checkpoint.
