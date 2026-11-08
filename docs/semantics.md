# Semantics

What planlens-agent checks, exactly, in schema version 1. Where this document
and the code disagree, that is a bug; please report it.

## Three documents, two owners

| document | owner | trust | format |
| --- | --- | --- | --- |
| action catalog | application owner | trusted | `.toml` or `.json`, schema `planlens-agent/catalog/v1` |
| contract | application owner | trusted | `.toml` or `.json`, schema `planlens-agent/contract/v1` |
| candidate plan | the agent | **untrusted** | `.json` (or a mapping via the SDK), schema `planlens-agent/plan/v1` |

The catalog and contract are supplied independently of the plan. Nothing in a
plan can add, remove, waive, version or redefine anything in them.

JSON Schemas for all three, and for reports and fixtures, ship in the package
under `planlens_agent/schemas/`. The engine's own validation is authoritative;
tests check that every plan the engine passes also satisfies the schema, and
that every schema-invalid plan is not passed.

## Catalog

```toml
schema = "planlens-agent/catalog/v1"
id = "release-basic"
version = "1.0.0"

[actions.build]
outputs = { artifact = "release-artifact" }      # output name = artifact type

[actions.deploy]
inputs = { artifact = "release-artifact" }       # input name = accepted type
```

- Action ids: `^[a-z][a-z0-9_]{0,63}$`. Input and output names likewise.
- **Every declared input is required.** Optional inputs are not supported.
- The catalog says what an action is *declared* to consume and produce. It
  does not show what the code behind the action does.

## Contract

```toml
schema = "planlens-agent/contract/v1"
id = "release-basic"
version = "1.0.0"
catalog = { id = "release-basic", version = "1.0.0" }   # must match the catalog
required_actions = ["build", "code_review", "deploy"]

[[rules]]
id = "review-before-deploy"
kind = "prerequisite"
action = "deploy"
requires_prior = "code_review"
because = "Shown to the developer when the rule is broken."

[[rules]]
id = "deploy-what-was-tested"
kind = "same_artifact"
inputs = ["unit_tests.artifact", "deploy.artifact"]
```

**Every rule is required.** There are no optional or advisory rules.

| element | meaning |
| --- | --- |
| `required_actions` | each listed action appears in at least one step |
| `prerequisite` | **every** step of `action` has at least one step of `requires_prior` at an **earlier** position. If `action` does not appear, the rule has nothing to check. If `requires_prior` is missing or later, it is violated - leaving the prerequisite out does not satisfy it |
| `same_artifact` | every step input listed (as `action.input`) that resolves to a planned artifact resolves to the **same** one; see the matching scope below |
| any other `kind` | **unsupported**: reported, never skipped. Supported rules are still evaluated, but the check cannot pass |

### Repeated actions

Every occurrence is evaluated. A prerequisite is checked for **each** step of
`action`, and each one without an earlier `requires_prior` step is its own
finding: `deploy, review, deploy` gives one finding (the first deploy), and two
deploys with no review give two.

### `same_artifact`: matching scope

- **Members.** For each `action.input` listed, every step in the plan whose
  action is `action` contributes its binding of `input`. There is no limit on
  how many steps contribute; a plan may deploy twice, and both deploys are
  members.
- **Requirement.** All members that resolve must resolve to one planned
  artifact: one producer step and one output name.
- **No concealment.** The rule compares the set of identities, not pairs. Two
  matching members do not hide a third that differs, and an earlier matching
  occurrence does not hide a later one.
- **One finding per rule.** When members disagree, one `plan/artifact-identity`
  finding is reported, located at the first member (in plan order) whose
  identity differs from the first member's. `observed` lists **every** member
  and what it refers to. The checker cannot tell which member is the wrong one.
- **Absent members.** A listed action that does not appear contributes nothing.
  Requiring it to appear is `required_actions`' job.
- **Unresolved members** (missing step, missing output, unknown producer
  action, or an unbound input) are left out of the comparison, because they
  have no identity. Each is reported as its own violation, so such a plan is
  rejected and never passes on account of the omission.
- **Forward references** still have an identity and are compared; they are
  also rejected separately as `plan/reference-before-producer`.

Limitations, by design in version 1:

- The scope is the **whole plan**. A plan that legitimately sends different
  artifacts through the same actions - two services released together - cannot
  satisfy one rule. There is no per-release or per-branch grouping.
- Identity is the *planned* producer and output, not bytes, digests or
  provenance.

### A validated policy is fixed

`validate_policy()` returns a policy whose catalog and contract are read-only
all the way down - read-only mappings, tuples and frozen records - and are
private copies: changing the files or mappings it was built from afterwards has
no effect. Each document records its digest and a binding of that digest to the
parsed model. `check(policy=...)` and the runner verify that binding before use,
so a policy rebuilt with `dataclasses.replace` under its old identity is refused
with `PolicyIntegrityError`. To change a policy, change the document and
validate it again; it gets a new identity. This guards against accidental and
`replace`-style changes, not against code deliberately forging the binding.

### Policy validation, before any plan is read

The contract and catalog are validated together first. Any of these makes the
policy unusable and the check end with exit 2 without evaluating the plan:

- schema errors in either document, including unknown fields;
- a catalog pin that does not match the supplied catalog (`contract/catalog-mismatch`);
- a required action, rule action, or rule input not in the catalog (`contract/unknown-action`);
- prerequisites that form a cycle, including an action requiring itself (`contract/cycle`);
- a `same_artifact` rule over inputs of different artifact types (`contract/inconsistent-artifact-rule`);
- a required action that can never run (`contract/unsatisfiable-input`): some
  input type is produced by no action that can itself run before it. Actions
  with no inputs can run first; an action can run once every input type it
  needs is produced by an action that can already run. So `produce: seed ->
  result` of one type is refused on its own, and accepted once an independent
  action produces the seed. Prerequisite rules are not part of this check, so
  it never refuses a contract that a plan could satisfy.

An unsupported rule kind does not stop evaluation, but the verdict is `error`
(exit 2) with error kind `unsupported`.

## Plan

```json
{
  "schema": "planlens-agent/plan/v1",
  "steps": [
    {"id": "build_rc", "action": "build"},
    {"id": "deploy_prod", "action": "deploy", "inputs": {"artifact": "build_rc.artifact"}}
  ]
}
```

- **List order is the proposed execution order.** There is no other ordering.
- Step ids are unique, `^[A-Za-z][A-Za-z0-9_-]{0,63}$`, ASCII only.
- An input binds to `"<step_id>.<output>"`: an output of a *producer step*.
  That pair - producer step and output name - is the **planned artifact
  identity**.
- `title` and `description` are free text, any Unicode, never interpreted.
- Two steps with no rule between them may appear in either order.

### What a plan may not contain

| fields | treated as | exit |
| --- | --- | --- |
| `waive`, `waivers`, `skip_rules`, `disable_rules`, `contract`, `catalog`, `policy`, `rules`, `required_actions`, `overrides`, `*_version` at the top level; `waive`, `skip_checks`, `harmless`, `safe`, `trusted`, `capabilities`, `effects`, `outputs`, `approved`, `approval_granted`, ... on a step | `plan/policy-override` **violation**; the field is not honoured and every requirement is still evaluated | 1 |
| `mode` other than `"sequential"`, `edges`, `parallel`, `graph`; on a step `depends_on`, `after`, `when`, `if`, `condition`, `loop`, `retry`, `parallel_group`, ... | `plan/unsupported-semantics`; the plan is **not evaluated**, because reading it as a sequential list could pass a plan that would run differently | 2 |
| any other unknown field | `plan/schema` | 2 |

Candidate-declared dependencies (`depends_on`) are refused rather than
ignored: requirements come only from the owner's contract.

### Input versus violation

The line is drawn at the schema.

- **Input errors (exit 2)**: unreadable or malformed documents, duplicate JSON
  keys, wrong types, unknown fields, bad identifier or reference *syntax*,
  duplicate step ids, size limits, unsupported schema versions.
- **Violations (exit 1)**: the plan is well-formed, but an action is unknown,
  a reference does not resolve or points forward, an input is missing or of the
  wrong type, a required action is absent, a prerequisite is not earlier, the
  artifact identities differ, or the plan tries to override policy.

Duplicate step ids are an input error because every reference that names the
id becomes ambiguous.

### Limits, checked before expensive work

| limit | value |
| --- | --- |
| document size | 1,000,000 bytes, checked before parsing |
| nesting depth | 32 |
| steps per plan | 1,000 |
| inputs per step, inputs/outputs per action | 32 |
| actions per catalog, rules per contract | 500 |
| integers | 4,300 digits, whatever the host's `sys.set_int_max_str_digits()` |
| identifiers | 64 characters |
| free text | 2,000 characters |

No input is executed or evaluated as code.

### How input problems are classified

One principle applies to every document, through the CLI and the SDK alike:

| rule | meaning | examples |
| --- | --- | --- |
| `<doc>/malformed` | not valid JSON or TOML text, or ambiguous text | syntax errors (with line and column), duplicate JSON keys, the `NaN`/`Infinity` tokens, which are not JSON |
| `<doc>/schema` | valid text whose value is outside the JSON value model or the schema | `1e400` (valid JSON that overflows to infinity), a JSON `"\ud800"` escape (an unpaired surrogate is not Unicode text), TOML `inf`/`nan`, TOML dates, unknown fields, wrong types |
| `<doc>/limit` | too large | file size, nesting depth, integers over 4,300 digits |

The CLI reads files, so it shares the SDK's file path exactly. An SDK caller may
also pass a mapping. A mapping has no text, so it can never be `malformed`: the
same values are reported as `schema` or `limit`, and duplicate keys cannot
occur. Size is measured differently for the two: a file is measured in raw
bytes **before** parsing, and a mapping by its canonical JSON encoding. A file
padded with whitespace can therefore exceed the limit while the same content
passed as a mapping does not.

## Verdicts and exit codes

| verdict | exit | when |
| --- | --- | --- |
| `pass` | 0 | the plan satisfies every supported, required check |
| `reject` | 1 | at least one violation, and nothing invalid or unsupported |
| `error` | 2 | an invalid catalog, contract or plan, or unsupported semantics or rules |

When several apply, the more serious wins: invalid catalog, invalid contract,
invalid plan, unsupported, then violation. An internal bug is also exit 2 and
never a pass.

A **pass** is a statement about the declared plan only. It does not show that
any step ran or succeeded, that tests passed, that review or approval
happened, what bytes were deployed, or that the contract captures every
requirement the owner has.

## Reports

One diagnostic shape is used by the SDK (`Diagnostic`), the CLI, the inspector, SARIF and saved JSON
reports (`planlens-agent/report/v1`):

| field | meaning |
| --- | --- |
| `rule_id`, `rule_version` | stable identity of the rule; `planlens-agent explain <rule_id>` |
| `category` | `violation`, `invalid_plan`, `invalid_contract`, `invalid_catalog` or `unsupported` |
| `message` | one specific sentence |
| `observed`, `required` | the concrete values compared |
| `location` | `document`, logical `path` (e.g. `/steps/3/inputs/artifact`), `step_id`, `step_index`, `source_span` |
| `location_absent_reason` | why there is no location, e.g. a required step that does not exist |
| `related_locations` | other places involved, including the contract rule |
| `contract_rule` | the owner's rule id |
| `explanation`, `suggestion` | what the rule means; a proposed next step |
| `evidence` | further facts, such as the owner's `because` text |

**Locations.** `step_index` is the 0-based position in `steps` - the proposed
execution position - and is never presented as a line number. `source_span`
(line and column) is filled only when a parser reports one, which in this
version means JSON and TOML syntax errors; otherwise it is `null`.

**Grouping and causes.** The text output groups findings by step. Every
finding is kept; a plan with four problems reports four. The report's
`earliest_step_with_finding` is the earliest position with a finding - not a
claim about which problem caused the others. For `plan/artifact-identity` the
checker reports *where* references disagree, and says it cannot tell which
reference is the wrong one.

**Suggestions** are proposals for a person to apply. Nothing is repaired
automatically, and a changed plan must be checked again.

**Determinism.** Same inputs, same report, byte for byte: no timestamps, no
host data, no randomness, no network. Digests are SHA-256 over a canonical
JSON encoding (sorted keys, no whitespace, UTF-8), so a contract written in
TOML and the same contract in JSON have the same digest.

**Escaping.** JSON reports are ASCII (`\uXXXX` escapes). Text output escapes
control and formatting characters, including terminal escape sequences and
bidirectional overrides, found in any input string.
