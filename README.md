# planlens-agent

**Lint AI-agent plans before they run.**

For developers building plan-and-execute agents. Your agent proposes a
structured plan; you supply the workflow requirements; `planlens-agent` checks
the plan against them before anything executes, tells you specifically what is
wrong, and shows which candidates got worse after you change a planner, model or
prompt.

> Pre-release, checkpoint B2. Not published to PyPI. The name `planlens-agent`
> is provisional - see [docs/naming.md](docs/naming.md). Offline: no model
> account, no network, no telemetry. The bundled plans are **authored examples**,
> not output from any model.

The journey: [check one plan](#1-check-one-plan), [understand the
diagnosis](#2-understand-the-diagnosis), [compare candidate
runs](#3-compare-candidate-runs), [integrate the gate](#4-integrate-the-gate).

A complete-looking plan can still be wrong. Every required step below is
present - and the release would deploy before it is reviewed:

```console
$ planlens-agent check plans/reversed-review.json
planlens-agent 0.1.0.dev0 - declared-plan check
  plan      plans/reversed-review.json  sha256:081faecf3fd5
  contract  release-basic 1.0.0  sha256:7bb60e9c3ee7
  catalog   release-basic 1.0.0  sha256:e85a33811dfb

RESULT: REJECTED (exit 1) - 1 violation

steps[3]  id=deploy_prod
  [violation] plan/prerequisite-order  (contract rule: review-before-deploy)
    steps[3] (deploy_prod) runs 'deploy' before any 'code_review'; 'code_review' appears later, at steps[4] (review)
    observed: {"later_occurrences": [{"step_id": "review", "step_index": 4}], "prior_occurrences": []}
    required: code_review before deploy
    at:       plan:/steps/3
    see:      plan:/steps/4
    see:      contract:/rules/0
    why:      A release may not be deployed before its code review.
    next:     Move step 'review' before step 'deploy_prod', or place a 'code_review' step earlier.
```

There is no demo recording yet. Every transcript in this README was captured
from the built package installed in a fresh environment outside this checkout.

## 1. Check one plan

Not on PyPI. Build and install the wheel (Python 3.11 or newer; no runtime
dependencies):

```console
$ git clone <this repository> && cd planlens-agent      # no public remote exists yet
$ uv build
$ python -m venv .venv && . .venv/bin/activate
$ pip install dist/planlens_agent-0.1.0.dev0-py3-none-any.whl

$ planlens-agent init                 # copies the release-basic example
$ cd release-basic
$ planlens-agent validate-policy
OK - contract release-basic 1.0.0 is valid against catalog release-basic 1.0.0: 5 required actions, 3 prerequisites, 1 artifact-identity rule.
$ planlens-agent check plans/valid.json
...
RESULT: PASS (exit 0) - 6 steps satisfy every supported required check
```

The two-minute quickstart target from the project standard has not been measured
with a person yet.

| exit | meaning |
| --- | --- |
| 0 | the plan passes every supported required check |
| 1 | the plan is rejected |
| 2 | invalid input or configuration, unsupported required semantics, or a usage error |

## 2. Understand the diagnosis

Each finding names the rule, the step, what was observed, what was required, why,
and what to change. `planlens-agent explain plan/prerequisite-order` describes a rule
offline; [docs/rules.md](docs/rules.md) lists them all.

### Fix it and check again

Fix a copy - `fixtures.toml` records the files in `plans/` as expected outcomes:

```console
$ cp plans/reversed-review.json my-plan.json
$ # move the "review" step above "deploy_prod" in my-plan.json
$ planlens-agent check my-plan.json
RESULT: PASS (exit 0) - 5 steps satisfy every supported required check
```

The second bundled failure is subtler. Tests and the scan use one build; the
deploy uses another:

```console
$ planlens-agent check plans/artifact-mismatch.json
...
  [violation] plan/artifact-identity  (contract rule: deploy-what-was-verified)
    steps[5] (deploy_prod) input 'artifact' refers to build_hotfix.artifact, but steps[2] (test_rc) input 'artifact' refers to build_rc.artifact
    ...
    why:      The artifact that is deployed must be the one that was tested and scanned.
```

### What you write

The **catalog** lists actions and the artifacts they consume and produce. The
**contract** says what every plan must satisfy. Both are yours; the agent only
writes plans and cannot change either.

```toml
# contract.toml
required_actions = ["build", "unit_tests", "security_scan", "code_review", "deploy"]

[[rules]]
id = "review-before-deploy"
kind = "prerequisite"
action = "deploy"
requires_prior = "code_review"
because = "A release may not be deployed before its code review."

[[rules]]
id = "deploy-what-was-verified"
kind = "same_artifact"
inputs = ["unit_tests.artifact", "security_scan.artifact", "deploy.artifact"]
```

```json
{"schema": "planlens-agent/plan/v1",
 "steps": [
   {"id": "build_rc", "action": "build"},
   {"id": "test_rc", "action": "unit_tests", "inputs": {"artifact": "build_rc.artifact"}},
   ...
 ]}
```

List order is execution order. Steps with no rule between them may come in
any order. Full rules: [docs/semantics.md](docs/semantics.md).

### See a check as a report

```console
$ planlens-agent inspect plans/artifact-mismatch.json --output report.html
wrote report.html: REJECTED (exit 1), 1 finding
```

One self-contained HTML file: the verdict, the proposed sequence, the requirement that
failed, the artifact references involved and what to change next. Select a finding to
focus the steps it involves. It loads nothing and needs no server. It records one
check; to change the result, edit the plan and check again.
See [docs/inspector.md](docs/inspector.md).

## 3. Compare candidate runs

After changing your planner, model or prompt, which candidate plans newly violate your
workflow requirements? You supply the candidates; nothing here calls a model. List them in
a suite, save a run, and compare it with an earlier one:

```console
$ planlens-agent suite run suite.toml --label planner=example-a --output v1.run.json
...
RESULT: REJECTED (exit 1) - 5 cases: 3 passed, 2 rejected, 0 could not be evaluated

  pass    standard#1
  pass    standard#2
  reject  hotfix      plan/artifact-identity at deploy_prod
  pass    expedite
  reject  legacy      plan/prerequisite-order at deploy_prod

A saved run is a record, not an approval.

$ planlens-agent suite run suite.toml --plans-dir candidates/v2 --label planner=example-b --output v2.run.json
$ planlens-agent suite diff v1.run.json v2.run.json
...
RESULT: NEW VIOLATIONS (exit 1)
  1 new, 1 resolved, 1 unchanged, 0 changed evidence, 0 ambiguous
  5 cases compared, 0 added, 0 removed, 0 not comparable
  plans: 5 changed, 0 identical
  candidate run: rejected, 2 case(s) rejected

New findings
  + expedite: plan/prerequisite-order (review-before-deploy) at deploy_prod
      steps[3] (deploy_prod) runs 'deploy' before any 'code_review'; 'code_review' appears later, at steps[4] (review)

Resolved findings
  - hotfix: plan/artifact-identity (deploy-what-was-verified) at deploy_prod

Unchanged findings
  = legacy: plan/prerequisite-order (scan-before-deploy) at deploy_prod  (moved steps[3] -> steps[4])
```

`candidates/v1` and `v2` are authored stand-ins for two planner versions. `legacy` gained a
step, so its finding moved; findings are matched by identity, not position, so it stays one
unchanged finding.

| command | exit 0 | exit 1 | exit 2 |
| --- | --- | --- | --- |
| `suite run` | every case passes | analysis finished, a case is rejected | invalid input, or the run is incomplete |
| `suite diff` | no new violations | new violations | invalid run, not comparable, or incomplete comparison |

**No new violations is not a pass, and neither is an approval.** Comparing two identical
rejected runs exits 0 and says so:

```console
$ planlens-agent suite diff v1.run.json again.run.json
...
RESULT: NO NEW VIOLATIONS (exit 0) - REPLAY of identical plans
  0 new, 0 resolved, 2 unchanged, 0 changed evidence, 0 ambiguous
  5 cases compared, 0 added, 0 removed, 0 not comparable
  plans: 0 changed, 5 identical
  candidate run: rejected, 2 case(s) rejected
...
Every candidate plan is identical to its baseline plan, so this is a replay of the same content. It says nothing about a new planner, model or prompt, whatever the labels say.
```

So gate on `suite run`, and use `suite diff` to see what changed. A dropped case is never
counted as resolved. If the contract, catalog or evaluator build differ, the comparison is
`not_comparable` (exit 2), and the difference is not attributed to your plans. Labels are
caller-supplied and unverified. Full semantics: [docs/suites.md](docs/suites.md).

## 4. Integrate the gate

### Python SDK

The same check, the same diagnostics:

```python
from planlens_agent import check, validate_policy

policy = validate_policy("contract.toml", "catalog.toml")  # validate once
result = check(agent_plan, policy=policy)  # a dict or a path
result.verdict  # "pass" | "reject" | "error"
result.exit_code  # 0 | 1 | 2
for d in result.diagnostics:
    print(d.rule_id, d.location.path if d.location else d.location_absent_reason)
    print(d.message, d.suggestion)
result.to_json()  # identical to `planlens-agent check --format json`
```

Run the full example from an `init` directory: [`examples/sdk_check.py`](examples/sdk_check.py).

### Run only what passed

```python
from planlens_agent import ReferenceRunner, check

runner = ReferenceRunner("contract.toml", "catalog.toml", handlers)
approval = check(plan, "contract.toml", "catalog.toml").approve()  # raises unless pass
runner.dispatch(approval, payload=plan)
```

The runner executes the exact snapshot that passed. Observed, from
[`examples/guarded_runner.py`](examples/guarded_runner.py):

```text
2. a rejected plan cannot be approved, so nothing runs:
  plan not approved: verdict is 'reject' with 1 finding(s); a rejected or unevaluated plan cannot be dispatched
3. a payload changed after the check is blocked until it is checked again:
  dispatch blocked: the presented payload (sha256:7bf23677bf29) is not the approved plan (sha256:a100bb5a50d7); check it again to obtain a new approval (dispatched: 0)
```

This is a library: code that never calls it is not constrained by it, and a
SHA-256 digest identifies content without showing who produced it, that anyone
approved it, or that it ran. See [docs/integration.md](docs/integration.md).

### In a LangGraph graph

[`examples/langgraph_gate`](examples/langgraph_gate) is a real graph, tested with LangGraph
1.2.20: planner, PlanLens check, then execute or reject. A rejected plan reaches no handler,
and a plan changed after the check is blocked. The fixture planner in it is labelled as
one; pass your own callable to connect a real planner. It gates one sequential plan, not
arbitrary LangGraph workflows, parallel graphs or tool traces. LangGraph is a dependency of
that recipe only.

### In CI

```console
$ planlens-agent suite run suite.toml --output candidate.run.json    # the gate
$ planlens-agent suite diff baseline.run.json candidate.run.json     # what changed
$ planlens-agent check plan.json --format json --output report.json  # or a single plan
```

With `--format json`, stdout carries only the report. No colour or ANSI escapes are ever
emitted. Keep the baseline as an owner-reviewed file: `suite run` refuses to replace an
existing run unless you pass `--overwrite`, and nothing should write a candidate over it
automatically. Regression fixtures for the checker itself: `planlens-agent regress
fixtures.toml`.

The workflows in [`.github/workflows`](.github/workflows) and their exact local commands are
in [docs/ci.md](docs/ci.md). **They have not run on a hosted service**, because no remote
exists yet.

SARIF 2.1.0 for tools that read it (validated against the official schema; ingestion
by GitHub code scanning has **not** been tested, see [docs/sarif.md](docs/sarif.md)):

```console
$ planlens-agent check plan.json --format sarif --output plan.sarif --source-root .
```

## What a pass does and does not mean

A pass means **the declared plan** satisfies every supported, required check in
your contract and catalog. It does not show that any step ran or succeeded,
that tests passed, that a review or approval happened, what was deployed, or
that your contract captures every requirement you have. "Same artifact" means
the same planned producer step and output, not the same bytes.

Requirements the engine cannot evaluate are never silently passed: an unknown
rule kind, or a plan asking for parallel, conditional or looping execution,
ends in exit 2.

## Status

| | |
| --- | --- |
| verified locally | macOS 26.6.2 arm64; CPython 3.14.7 and 3.11.15; wheel installed outside the checkout; suite journey and LangGraph 1.2.20 recipe. Inspector rendered in Chrome 156 (not re-rendered in B2; its output is byte-identical to B1) |
| verified by independent review of B2-r2, not of later repairs | Linux x86_64, CPython 3.12.14 |
| prepared, not run | hosted CI, the consumer demo workflow, SARIF upload |
| not yet verified | suites and the recipe on Linux; Windows; CPython 3.12 and 3.13 for B2; other browsers; SARIF ingestion by any service |
| not supported | parallel/DAG plans, loops, conditions, natural-language plans, runtime evidence of execution or approval, plugins, signed run files |
| evidence of use | none yet. No external user has tried it, and no real planner or model output has been run through it |

## Documentation

- [docs/semantics.md](docs/semantics.md) - contract, catalog, plan and report semantics
- [docs/rules.md](docs/rules.md) - every rule (also `planlens-agent explain <rule>`)
- [docs/suites.md](docs/suites.md) - suites, saved runs and comparison semantics
- [docs/integration.md](docs/integration.md) - binding a check to execution, and its limits
- [examples/langgraph_gate](examples/langgraph_gate/README.md) - the LangGraph recipe
- [docs/ci.md](docs/ci.md) - workflows, local commands and baselines
- [docs/inspector.md](docs/inspector.md) - the HTML report
- [docs/sarif.md](docs/sarif.md) - SARIF export and what has been verified
- [docs/acceptance-examples.md](docs/acceptance-examples.md) - authored acceptance cases
- [docs/naming.md](docs/naming.md) - provisional names and what was checked
- [PROVENANCE.md](PROVENANCE.md) - relationship to the
  [PlanLens research artifact](https://github.com/nurdauletabdurahman777-maker/planlens-icdm2026)

## Development

```console
$ uv venv && uv pip install -e ".[dev]"
$ python -m pytest
$ python scripts/ci_local.py all --python 3.14    # every CI stage, needs uv
$ python scripts/gen_rule_docs.py && python scripts/gen_acceptance_doc.py
```

Licensed under the Apache License 2.0; see [LICENSE](LICENSE) and
[NOTICE](NOTICE). The OASIS SARIF schema under `tests/data/sarif/` is a separately licensed
test resource, outside Apache-2.0 and outside the wheel; see its `NOTICE.md`.
