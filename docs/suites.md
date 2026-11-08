# Suites, saved runs and comparison

The question: **after changing my planner, model or prompt, which candidate plans newly
violate my workflow requirements?**

You supply the candidates. `planlens-agent` calls no model, infers no requirement, and
evaluates each case with `check()`, so a suite finding is the same finding a single check
gives. `regress` is different: it tests recorded expectations about the checker.

## Suite file

```toml
schema = "planlens-agent/suite/v1"
id = "release-candidates"
contract = "contract.toml"       # relative to this file
catalog = "catalog.toml"
plans_dir = "candidates/v1"      # default: this file's directory

[[cases]]
id = "standard"
sample = "1"                     # optional: separate trials of one case
plan = "standard-1.json"         # relative to plans_dir
```

`.toml` or `.json`. A case is identified by `id` and `sample`, so three trials of one case
are three separate results. Every listed case is required. Limits: 2,000 cases, 32 labels,
200 characters per label value.

## `suite run`

```console
planlens-agent suite run suite.toml [--plans-dir DIR] [--label NAME=VALUE]...
                                    [--output FILE] [--overwrite] [--format text|json]
```

`--plans-dir` reads the same cases from another directory, relative to the current directory.

| exit | status | meaning |
| --- | --- | --- |
| 0 | `passed` | every case passes |
| 1 | `rejected` | every case was evaluated and at least one is rejected |
| 2 | `incomplete` | at least one case could not be evaluated, or an entry was invalid |
| 2 | none | the suite file is unusable or a label is invalid: nothing ran, nothing was written |

Nothing is dropped quietly:

- A missing, unreadable or invalid plan becomes an `error` case with its structured finding.
  Other results are kept, and the run is `incomplete`.
- An invalid entry is recorded in `suite_errors`, and the other cases still run.
- A repeated case key is recorded in `suite_errors` and **neither** entry is evaluated,
  because nothing says which one is the case.
- `incomplete` outranks `rejected`: a rejection does not hide an unevaluated case.

`--output` refuses to replace an existing file unless you pass `--overwrite`, because a
baseline is a reviewed input. `--overwrite` may replace an earlier report, never an input: the
suite manifest, its contract and catalog, and the candidates this invocation reads, including
those under `--plans-dir`, are refused with exit 2. See [`--output` and inputs](#--output-and-inputs).

## What a run records

`planlens-agent/run/v1`: for each case the plan path, digest, step count and canonical
content, and the verdict and findings; for the run, the suite id and digest, contract and
catalog ids, versions and digests, report schema, every rule version, the engine version, an
evaluator digest, and the Python version. Runs contain no timestamp, so repeating a run gives
identical bytes. `plans_dir` is recorded as given, so an absolute `--plans-dir` puts a local
path in the file.

- **Evaluator digest.** SHA-256 over the modules that decide verdicts, which tells apart
  development builds sharing the version string `0.1.0.dev0`. Any edit to those modules
  changes it, including a harmless one: it shows that the evaluator changed, not that its
  behaviour did. If the source cannot be read it is `null`, and comparisons refuse it.
- **Labels** such as `--label model=x` are metadata supplied by the caller. Nothing verifies
  them, and they do not establish which model, prompt or provider produced a plan.
- **A run is a record, not an approval.** `ReferenceRunner.dispatch` refuses it. Execution
  still needs `check(...).approve()` in the same process.

## Reading a saved run

Runs are untrusted input. Reading one enforces a 64,000,000-byte limit, UTF-8, no duplicate
keys, no non-finite numbers, 64 levels of nesting, exact fields and types, unique case keys,
each plan's content against its digest, each finding's rule version against the run's `rules`,
and verdicts, status and summary against the cases. A problem raises `RunFileError`, and the
command exits 2.

These are separate questions, with separate outcomes:

| question | outcome |
| --- | --- |
| Is it parseable and well formed? | no: `RunFileError`, exit 2 |
| Can this engine read its formats? | unknown `run_schema` or engine: `RunFileError`, exit 2. Unknown `report_schema`: `not_comparable`, exit 2 |
| Who produced it? | not established; see below |

This catches corruption and careless edits. **It is not authentication.** Run files are not
signed: someone who edits a run consistently, including its digests, is not detected. Protect
baselines the way you protect other reviewed files.

## `suite diff`

```console
planlens-agent suite diff BASELINE CANDIDATE [--output FILE] [--format text|json]
```

`--output` must not be either input.

### Matching findings

Findings are compared within a case and sample, by identity:

| part | value |
| --- | --- |
| rule | `rule_id` |
| requirement | `contract_rule` |
| anchor | step id and field for a plan finding; document path otherwise; the requirement itself when there is no location |

`plan/artifact-identity` yields at most one finding per contract rule, so that rule alone
identifies it, whichever step it points at. Positions are not part of identity. Evidence is
compared without positions, plan-order lists, or message wording, because messages contain
positions.

Within one identity, findings with identical evidence pair first. Then:

| remaining | result |
| --- | --- |
| only in the candidate | `new` |
| only in the baseline | `resolved` |
| one on each side | `changed_evidence`: same requirement, different facts |
| more than that | `ambiguous`: reported, never collapsed, and the comparison is incomplete |

An unchanged finding that moved records both positions.

### Accounting for cases

| case | effect |
| --- | --- |
| in both, both evaluated | compared |
| in the baseline only | `removed`: incomplete. Its findings are **not** resolved, passing or failing |
| in the candidate only, passing | `added`, reported |
| in the candidate only, rejected | `added`, and its findings count as new |
| an `error` on either side | `not_comparable`: incomplete. Its findings are neither new nor resolved |

### Status

The first that applies:

| status | exit | meaning |
| --- | --- | --- |
| `not_comparable` | 2 | suite id, contract, catalog, evaluator or rule versions differ or are unknown, or the report schema differs or is not one this engine supports |
| `incomplete_comparison` | 2 | removed or unevaluated cases, suite errors, or ambiguous findings |
| `new_violations` | 1 | like for like, with new findings |
| `no_new_violations` | 0 | like for like, every case accounted for, nothing new |

When runs are `not_comparable` the differences are still shown, under a statement not to
attribute them to the candidate plans. A changed suite file, engine version string or Python
version is noted and does not block a comparison.

### What exit 0 does not mean

`no_new_violations` describes change. It does not mean the candidate passes: two identical
rejected runs exit 0 while `suite run` still exits 1. Output always shows the candidate's own
status, and the JSON has `candidate_passes` and `"approval": false`. **Gate on `suite run`.**

### Replay

If every shared plan has the same canonical content (formatting aside) and nothing was added,
the result is marked as a replay. It says nothing about a new planner, model or prompt, whatever the labels
say. Replaying files is not evidence about a model.

## Observed examples

From `scripts/journey.py`, run against the installed wheel using the authored
`release-basic` candidates.

| scenario | command result |
| --- | --- |
| all candidates pass (`v3`) | `suite run` exit 0 |
| two candidates rejected (`v1`) | `suite run` exit 1 |
| one plan is not JSON | `suite run` exit 2; `plan/malformed` recorded; 4 results kept |
| `expedite` deploys before review (`v1` to `v2`) | `suite diff` exit 1, one new finding |
| `hotfix` fixed (`v1` to `v2`) | one resolved finding |
| `legacy` gains a leading step (`v1` to `v2`) | one unchanged finding, moved `steps[3]` to `steps[4]` |
| a rejected case dropped from the suite | `suite diff` exit 2; removed; 0 resolved |
| a duplicate case id | `suite run` exit 2; `duplicate-case`; neither entry evaluated |
| contract version changed | `suite diff` exit 2, `not_comparable`, names the contract |
| evaluator digest differs | `suite diff` exit 2, `not_comparable`, names the evaluator |
| identical rejected runs | `suite diff` exit 0, replay, `candidate_passes: false`; `suite run` exit 1 |

## `--output` and inputs

`check`, `inspect`, `suite run` and `suite diff` never write a report over a file they read.
Identity is compared by file, not by name, so relative spellings, symlinks, hard links and case
variants on a case-insensitive filesystem are all refused with exit 2 before anything is written.

| command | protected inputs |
| --- | --- |
| `check`, `inspect` | the plan, contract and catalog, including the default `contract.toml` and `catalog.toml` |
| `suite run` | the suite manifest, its contract and catalog, and every candidate it lists under the directory in use |
| `suite diff` | both runs |

This protects against accidents in these commands. It does not stop anything else from changing
a file, and a file that is not an input of the current invocation can still be overwritten.

## Limits

- A renamed step is a new finding plus a resolved one. Step ids are the stable identity, and
  a rename is not followed.
- A step id reused for different work matches its old findings.
- `changed_evidence` does not fail a comparison. Read it: the same requirement is failing for
  different reasons.
- Comparison covers what `check()` can see: declared, ordered, sequential plans. It says
  nothing about what ran.
- Run files are not signed, and labels are not verified.
