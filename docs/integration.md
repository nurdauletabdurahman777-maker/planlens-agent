# Binding a check to execution

A check is only useful if the plan that runs is the plan that was checked.
`planlens-agent` provides one small piece of that: an approval object bound to
an exact snapshot, and a reference runner that refuses anything else.

## The flow

```python
from planlens_agent import ReferenceRunner, check

runner = ReferenceRunner("contract.toml", "catalog.toml", handlers)  # trusted policy
result = check(plan, "contract.toml", "catalog.toml")
approval = result.approve()  # raises NotApproved unless the verdict is "pass"
runner.dispatch(approval, payload=plan)
```

`check()` copies the plan the moment it is called. `approve()` turns that copy
into an `ApprovedPlan`: a read-only snapshot, its SHA-256, and the digests of
the contract, catalog and engine version it passed under. An `ApprovedPlan`
always holds its own read-only copy, even when built with `dataclasses.replace`
from a mapping the caller keeps.

The runner then re-checks that snapshot under its own policy and **executes the
read-only copy made by that re-check**. It never reads `approval.plan`, or a
presented payload, once dispatch begins, so changing either mid-dispatch has no
effect on what runs.

## What the runner refuses, before any handler runs

| situation | outcome |
| --- | --- |
| a rejected or errored check | `approve()` raises `NotApproved`; passing a `CheckResult` to the runner raises `DispatchBlocked` |
| the approval's snapshot was altered | `DispatchBlocked`: snapshot no longer matches its approved digest |
| a presented payload differs from the approved plan | `DispatchBlocked`, even if the changed payload would itself pass. Check it again to obtain a new approval |
| the approval was issued under a different contract, catalog or engine version | `DispatchBlocked` |
| a hand-built `ApprovedPlan` for a plan that does not pass | `DispatchBlocked`: the runner re-checks the snapshot under its own policy |
| an action in the plan has no registered handler | `DispatchBlocked`, before the first step |
| a policy with an unsupported rule | the runner refuses to be constructed |
| the runner's own policy was altered after construction | `DispatchBlocked` |

In every one of these cases zero handlers are entered; the tests assert this by
counting calls into the handlers themselves.

## Two different "changes after validation"

- **The caller mutates their own object after `check()`.** Harmless: the
  approval holds an independent snapshot, and the runner executes the
  snapshot. The mutation is simply not what runs.
- **A different payload is presented for execution under an earlier
  approval.** Blocked. The runner compares, never re-validates on the
  caller's behalf, and never runs the presented payload - only the snapshot.

## What happens during dispatch

Steps run one at a time, in plan order. Each handler receives a `StepCall` with
the read-only step and the values that earlier handlers returned for the
outputs it references. A handler must return exactly the outputs the catalog
declares for its action. If a handler raises or returns the wrong outputs, the
runner stops and reports which step failed. There are no retries and nothing is
repaired.

| | what it holds |
| --- | --- |
| `StepCall` | `step_id`, `action`, the read-only `step`, and `inputs`: each input name mapped to the value an earlier handler returned for the output it references |
| a handler's return value | a mapping of exactly the outputs the catalog declares for the action, or `None` when it declares none |
| `dispatch()` returns a `DispatchRecord` | `status` (`completed` or `handler_failed`), `dispatched` as `(step_id, action)` pairs in order, `failed_step`, `error` and the `binding` it ran under |

[`examples/guarded_runner.py`](../examples/guarded_runner.py) runs a passing plan, a rejected one, a changed payload
and a caller-side edit through these calls.

## Limits of this design

- **A library cannot stop a caller from bypassing it.** Code that executes plans
  without the runner, or executes some other object, is not constrained by any
  of this.
- **Anyone can construct an `ApprovedPlan`.** The runner's re-check stops a
  forged approval of a bad plan, but there is no signature; this is not an
  authorization system.
- **Digests identify content.** They do not show who produced the plan, that a
  person approved anything, or that anything ran.
- **Handlers are trusted code.** The runner dispatches to whatever is registered
  for an action name. The catalog describes what an action is declared to do;
  it does not verify the implementation.
- **Passing values between handlers is not artifact verification.** Whatever a
  build handler returns is what a deploy handler receives. Nothing checks bytes,
  signatures or provenance.
- This is a reference integration, not an orchestration engine: no parallelism,
  retries, scheduling, persistence or framework adapters.

## In a framework

[`examples/langgraph_gate`](../examples/langgraph_gate/README.md) puts this boundary inside a
LangGraph graph. It is a recipe, not an adapter: it gates one sequential plan and adds no
dependency to the core. Saved suite runs are records and cannot be dispatched; see
[suites.md](suites.md).
