# release-basic

A small, readable example: build a release, test it, scan it, review it,
deploy it. Every file here is **authored** for learning the tool. None of it
is output from a real agent.

| file | owned by | what it is |
| --- | --- | --- |
| `catalog.toml` | you, the application owner | the actions a plan may use, and the artifacts each consumes and produces |
| `contract.toml` | you, the application owner | what every plan must satisfy |
| `plans/*.json` | an agent (here: authored examples) | candidate plans, untrusted |
| `fixtures.toml` | you | expected outcomes, for regression testing |
| `suite.toml` | you | the candidate cases to run together |
| `candidates/v1,v2,v3/` | a planner (here: authored examples) | three sets of candidates, standing in for three planner versions |

The agent writes plans. It does not get to edit the catalog or the contract,
and a plan that tries to - for example with a `"waive"` field - is rejected.

## What the contract requires

- Every plan contains `build`, `unit_tests`, `security_scan`, `code_review`
  and `deploy`.
- `deploy` comes after `code_review`, `unit_tests` and `security_scan`.
- The tests, the scan and the deploy all use **the same planned artifact** -
  the same build step's `artifact` output.

Nothing orders `unit_tests`, `security_scan` and `code_review` among
themselves, so any order of those three is fine.

## Expected results

| plan | expected | exit | why |
| --- | --- | --- | --- |
| `plans/valid.json` | pass | 0 | satisfies every rule |
| `plans/valid-alternate-order.json` | pass | 0 | same rules, review first and scan before tests |
| `plans/reversed-review.json` | reject | 1 | `plan/prerequisite-order`: `deploy_prod` (steps[3]) deploys before `review` (steps[4]) |
| `plans/artifact-mismatch.json` | reject | 1 | `plan/artifact-identity`: tests and scan use `build_rc.artifact`, deploy uses `build_hotfix.artifact` |

`reversed-review.json` and `artifact-mismatch.json` look complete - every
required action is there. That is the point: a plan can contain all the right
steps and still be wrong.

## Try it

```console
planlens-agent validate-policy
planlens-agent check plans/reversed-review.json
```

Read the finding, then fix a **copy** of the plan - `fixtures.toml` records
the files in `plans/` as expected outcomes, so editing them in place makes the
regression fixtures report a change:

```console
cp plans/reversed-review.json my-plan.json
# move the "review" step above "deploy_prod" in my-plan.json, then:
planlens-agent check my-plan.json
```

It passes. If you do edit a file in `plans/`, `planlens-agent regress
fixtures.toml` will show that expectation as UNMET - which is the fixtures
doing their job.

```console
planlens-agent check plans/reversed-review.json --format json   # the same result, as JSON
planlens-agent regress fixtures.toml                           # all four expectations
planlens-agent explain plan/artifact-identity                  # offline rule help
```

`regress` reports whether each *expectation* was met. A rejected plan whose
rejection was expected is a met expectation, and it is still a rejected plan.

## What a pass does not mean

A pass means the declared plan satisfies the contract. It does not show that
any step ran, that the tests passed, that the review actually happened, or what
was deployed. "Same planned artifact" compares which build step a plan says it
will use, not bytes or digests.

## Compare candidate runs

`fixtures.toml` tests the checker's expectations. `suite.toml` answers a different
question: after a planner, model or prompt change, which candidates newly violate the
contract? The `candidates/` directories are **authored**: they stand in for three planner
versions, and no model produced them.

```console
planlens-agent suite run suite.toml --output v1.run.json
planlens-agent suite run suite.toml --plans-dir candidates/v2 --output v2.run.json
planlens-agent suite diff v1.run.json v2.run.json
```

| case | v1 | v2 | v3 |
| --- | --- | --- | --- |
| `standard` (samples 1 and 2) | pass | pass | pass |
| `hotfix` | reject: deploys a different artifact | pass (resolved) | pass |
| `expedite` | pass | reject: deploys before review (new) | pass |
| `legacy` | reject: deploys before the scan | same finding, one step later (unchanged) | pass |

`suite run` exits 1 for v1 and v2 and 0 for v3. Comparing v1 with v2 exits 1 because of
`expedite`. Comparing v1 with itself exits 0 with no new violations while the run is still
rejected: no regression is not a pass, and neither is an approval.
