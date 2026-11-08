# Acceptance examples

Generated from `tests/acceptance/cases.toml` by `scripts/gen_acceptance_doc.py`.

These are **authored software fixtures** covering the Open-Source Standard,
section 7. They test that the checker behaves as specified on cases written
for that purpose. They are not observations of any agent or model, and they
say nothing about how often real plans contain these problems.

Each case runs through the SDK and the CLI, which must agree exactly
(`tests/test_acceptance.py`). Unless stated, the policy is the bundled
`release-basic` contract and catalog.

| # | case | input | policy | verdict | findings (exact counts) | location |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | valid-release | `valid.json` | `release-basic` | pass | none | - |
| 2 | independent-checks-in-another-order | `valid-alternate-order.json` | `release-basic` | pass | none | - |
| 3 | complete-plan-deploys-before-review | `reversed-review.json` | `release-basic` | reject | `plan/prerequisite-order` x1 | `/steps/3` (deploy_prod) |
| 4 | tests-and-deploy-use-different-artifacts | `artifact-mismatch.json` | `release-basic` | reject | `plan/artifact-identity` x1 | `/steps/5/inputs/artifact` (deploy_prod) |
| 5 | compound-missing-unknown-and-reversed | `compound.json` | `release-basic` | reject | `plan/missing-required-action` x1, `plan/unknown-action` x1, `plan/prerequisite-order` x2 | `/steps/4/action` (migrate); `/steps/2` (deploy_prod); `plan/missing-required-action`: none, by design |
| 6 | cyclic-contract | `valid.json` | `cyclic.toml` | error (invalid_contract) | `contract/cycle` x1 | `/rules/0` |
| 6 | contract-requires-an-action-the-catalog-lacks | `valid.json` | `unknown-action.toml` | error (invalid_contract) | `contract/unknown-action` x1 | `/required_actions/4` |
| 6 | contract-pinned-to-another-catalog | `valid.json` | `catalog-mismatch.toml` | error (invalid_contract) | `contract/catalog-mismatch` x1 | - |
| 7 | unresolved-references | `unresolved-reference.json` | `release-basic` | reject | `plan/unresolved-reference` x2 | `/steps/1/inputs/artifact` (test_rc); `/steps/4/inputs/artifact` (deploy_prod) |
| 7 | artifact-consumed-before-it-is-produced | `reference-before-producer.json` | `release-basic` | reject | `plan/reference-before-producer` x1 | `/steps/0/inputs/artifact` (test_rc) |
| 7 | duplicate-step-id | `duplicate-step-id.json` | `release-basic` | error (invalid_plan) | `plan/duplicate-step-id` x1 | `/steps/2/id` (check) |
| 7 | malformed-candidate | `malformed.json` | `release-basic` | error (invalid_plan) | `plan/malformed` x1 | line 5, column 46 |
| 8 | candidate-tries-to-waive-review | `policy-override.json` | `release-basic` | reject | `plan/policy-override` x2, `plan/missing-required-action` x1, `plan/prerequisite-order` x1 | `/waive`; `/steps/3/approved` (deploy_prod); `plan/missing-required-action`: none, by design |
| 10 | contract-requires-an-unsupported-rule | `valid.json` | `unsupported-rule.toml` | error (unsupported) | `contract/unsupported-rule` x1 | - |
| 10 | plan-asks-for-unsupported-semantics | `unsupported-semantics.json` | `release-basic` | error (unsupported) | `plan/unsupported-semantics` x1 | `/steps/4/when` (deploy_prod) |
| 9 | plan changes after validation | runner tests | release-basic | blocked | `DispatchBlocked`, zero handlers entered | - |

Example 9 needs an execution step, so it lives in `tests/test_runner.py`:
a payload whose artifact reference changed after approval is blocked, a
changed payload is blocked even when it would pass on its own, and a
mutation of the caller's original object after approval does not change
what runs.

Negative controls: the unsupported-rule case passes once that rule is
removed, and removing the waiver from the policy-override case changes
none of the requirements found.
