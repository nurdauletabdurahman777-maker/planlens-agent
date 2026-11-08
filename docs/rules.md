# Rules

Generated from `src/planlens_agent/diagnostics.py` by `scripts/gen_rule_docs.py`.
The same text is printed offline by `planlens-agent explain <rule>`.

| category | meaning | exit code |
| --- | --- | --- |
| `violation` | the plan parsed but breaks the catalog or contract | 1 |
| `invalid_plan`, `invalid_contract`, `invalid_catalog` | a document cannot be used | 2 |
| `unsupported` | a requirement or semantics the engine cannot evaluate | 2 |

## violation

### `plan/artifact-identity`

*Inputs that must share one artifact do not* - rule version 1, exit code 1.

A contract rule says several inputs must refer to the same planned artifact - for example, the artifact that is deployed must be the one that was tested. These inputs resolve to different producer outputs.

Triggers it:

```
unit_tests uses build_a.artifact; deploy uses build_b.artifact
```

Satisfies it:

```
unit_tests and deploy both use build_a.artifact
```

Limits: Identity is the planned producer step and output name. It is not evidence that bytes exist, that tests passed, or what was deployed. The checker cannot tell which of the differing inputs is the wrong one.

### `plan/artifact-type`

*Referenced artifact has the wrong type* - rule version 1, exit code 1.

The referenced producer output has a different declared artifact type from the one this input accepts.

### `plan/missing-input`

*Required input is not bound* - rule version 1, exit code 1.

The catalog declares this input for the action, and the step does not bind it to a producer output. All declared inputs are required in this schema version.

### `plan/missing-required-action`

*A required action is absent* - rule version 1, exit code 1.

The owner contract requires this action, and no step performs it. Leaving a requirement out of the plan does not remove it.

Limits: There is no plan location, because the missing step does not exist. The related location points at the requirement in the contract.

### `plan/policy-override`

*Plan tries to change trusted policy* - rule version 1, exit code 1.

The plan contains a field that would waive a requirement, replace the contract or catalog, change their version, or declare what an action is or does. Those come only from the owner. The field is not honoured, and every requirement is still evaluated.

Triggers it:

```
{"waive": ["review-before-deploy"], "steps": [...]}
```

### `plan/prerequisite-order`

*Action runs without a prior prerequisite* - rule version 1, exit code 1.

A contract prerequisite says every step of one action must be preceded by a step of another. This step has no earlier step of the prerequisite action, either because it appears later or because it does not appear at all.

Triggers it:

```
build, unit_tests, deploy, code_review
```

Satisfies it:

```
build, unit_tests, code_review, deploy
```

Limits: Order is checked over the declared plan. It does not show that the prerequisite step succeeded or approved anything.

### `plan/reference-before-producer`

*Artifact is consumed before it is produced* - rule version 1, exit code 1.

An input refers to an output of a step at the same or a later position, so the artifact would not exist yet when this step runs.

Triggers it:

```
unit_tests (uses build_rc.artifact) placed before build_rc
```

### `plan/unknown-action`

*Step uses an action the catalog does not define* - rule version 1, exit code 1.

Each step must name an action from the owner's catalog. An unknown action has no trusted definition, so the step cannot be checked or dispatched.

Triggers it:

```
{"id": "s1", "action": "deploy_everything"}
```

Satisfies it:

```
{"id": "s1", "action": "deploy", ...}
```

### `plan/unknown-input`

*Input is not declared for this action* - rule version 1, exit code 1.

The step binds an input name the catalog does not declare for the action.

### `plan/unresolved-reference`

*Artifact reference does not resolve* - rule version 1, exit code 1.

An input refers to `<step_id>.<output>`, and either no step has that id, or that step's action does not declare that output in the catalog.

Triggers it:

```
"inputs": {"artifact": "build_rc.binary"}
```

Satisfies it:

```
"inputs": {"artifact": "build_rc.artifact"}
```

## unsupported

### `catalog/unsupported-version`

*Catalog schema version is not supported* - rule version 1, exit code 2.

The catalog declares a schema version this engine does not implement. It is refused rather than read with the wrong semantics.

### `contract/unsupported-rule`

*Contract requires a rule kind this engine does not support* - rule version 1, exit code 2.

The contract contains a rule whose kind this engine does not implement - for example, verifying approval evidence. Every rule in a contract is required, so the check reports that it cannot be evaluated instead of passing without it. Supported rules are still evaluated and reported.

Triggers it:

```
[[rules]]
id = "approval-recorded"
kind = "approval_evidence"
```

Limits: Supported kinds in this version: prerequisite, same_artifact.

### `contract/unsupported-version`

*Contract schema version is not supported* - rule version 1, exit code 2.

The contract declares a schema version this engine does not implement. It is refused rather than read with the wrong semantics.

### `plan/unsupported-semantics`

*Plan uses semantics this engine does not support* - rule version 1, exit code 2.

The plan asks for execution semantics outside sequential plans: parallel groups, conditions, loops, candidate-declared dependencies, or a non-sequential mode. The plan is not evaluated, because checking it as a sequential list could report a pass for a plan that would run differently.

Triggers it:

```
{"id": "deploy", "action": "deploy", "when": "tests_passed"}
```

Limits: Execution order is the list order. Requirements come only from the owner contract, never from the candidate.

### `plan/unsupported-version`

*Plan schema version is not supported* - rule version 1, exit code 2.

The plan declares a schema version this engine does not implement. It is refused rather than read with the wrong semantics.

## invalid_plan

### `plan/duplicate-step-id`

*Step identifiers are not unique* - rule version 1, exit code 2.

Two steps share an id. Artifact references name a producer step by id, so a duplicate makes every reference to it ambiguous.

Triggers it:

```
[{"id": "build", ...}, {"id": "build", ...}]
```

### `plan/limit`

*Plan exceeds a size limit* - rule version 1, exit code 2.

The plan is larger than the documented bounds, which are checked before any expensive processing.

### `plan/malformed`

*Plan is not well-formed* - rule version 1, exit code 2.

The plan is not valid JSON or TOML, or a JSON object repeats a key. Duplicate keys are rejected because different parsers keep different copies, so the document's meaning would be ambiguous.

Triggers it:

```
{"id": "a", "id": "b"}
```

Limits: A source span is given when the parser reports one.

### `plan/schema`

*Plan does not match its schema* - rule version 1, exit code 2.

A required field is missing, a field has the wrong type or pattern, or the plan contains a field this schema version does not define. Unknown fields are rejected rather than ignored so that a misspelt requirement cannot vanish.

### `plan/unreadable`

*Plan could not be read* - rule version 1, exit code 2.

The plan file does not exist, is not a regular file, or cannot be read as UTF-8.

Limits: Nothing in the document is evaluated.

## invalid_contract

### `contract/catalog-mismatch`

*Contract was written for another catalog* - rule version 1, exit code 2.

The contract pins a catalog id and version, and the supplied catalog differs. Checking against a different catalog could silently change what the rules mean.

### `contract/cycle`

*Prerequisites form a cycle* - rule version 1, exit code 2.

Prerequisite rules form a cycle, so no plan containing any action on the cycle can satisfy them. The contract is inconsistent.

Triggers it:

```
deploy requires prior review; review requires prior deploy
```

### `contract/inconsistent-artifact-rule`

*Artifact-identity rule can never be satisfied* - rule version 1, exit code 2.

A same-artifact rule lists inputs that accept different artifact types, so no single planned artifact can satisfy all of them.

### `contract/limit`

*Contract exceeds a size limit* - rule version 1, exit code 2.

The contract is larger than the documented bounds, which are checked before any expensive processing.

### `contract/malformed`

*Contract is not well-formed* - rule version 1, exit code 2.

The contract is not valid JSON or TOML, or a JSON object repeats a key. Duplicate keys are rejected because different parsers keep different copies, so the document's meaning would be ambiguous.

Triggers it:

```
{"id": "a", "id": "b"}
```

Limits: A source span is given when the parser reports one.

### `contract/schema`

*Contract does not match its schema* - rule version 1, exit code 2.

A required field is missing, a field has the wrong type or pattern, or the contract contains a field this schema version does not define. Unknown fields are rejected rather than ignored so that a misspelt requirement cannot vanish.

### `contract/unknown-action`

*Contract names an action the catalog lacks* - rule version 1, exit code 2.

A requirement refers to an action, or an action input, that is not in the catalog. It could never be satisfied, so the contract is refused before any plan is checked.

### `contract/unreadable`

*Contract could not be read* - rule version 1, exit code 2.

The contract file does not exist, is not a regular file, or cannot be read as UTF-8.

Limits: Nothing in the document is evaluated.

### `contract/unsatisfiable-input`

*Required action can never run* - rule version 2, exit code 2.

A required action takes an input that no finite sequential plan can produce before it: either no catalog action outputs that type, or every action that does needs, directly or through others, an input that cannot be produced first. Every plan would fail, so the contract and catalog are inconsistent.

Triggers it:

```
produce: inputs seed=artifact, outputs result=artifact; nothing else
```

Satisfies it:

```
add fetch_seed: outputs seed=artifact, which needs no inputs
```

Limits: Prerequisite rules are not taken into account, so a required action that can only be preceded by an unrunnable prerequisite is not reported here; plans will still fail it.

## invalid_catalog

### `catalog/limit`

*Catalog exceeds a size limit* - rule version 1, exit code 2.

The catalog is larger than the documented bounds, which are checked before any expensive processing.

### `catalog/malformed`

*Catalog is not well-formed* - rule version 1, exit code 2.

The catalog is not valid JSON or TOML, or a JSON object repeats a key. Duplicate keys are rejected because different parsers keep different copies, so the document's meaning would be ambiguous.

Triggers it:

```
{"id": "a", "id": "b"}
```

Limits: A source span is given when the parser reports one.

### `catalog/schema`

*Catalog does not match its schema* - rule version 1, exit code 2.

A required field is missing, a field has the wrong type or pattern, or the catalog contains a field this schema version does not define. Unknown fields are rejected rather than ignored so that a misspelt requirement cannot vanish.

### `catalog/unreadable`

*Catalog could not be read* - rule version 1, exit code 2.

The catalog file does not exist, is not a regular file, or cannot be read as UTF-8.

Limits: Nothing in the document is evaluated.
