# Evidence and limits

What has been observed, what has not, and how far each result goes.

| Area | Status |
| --- | --- |
| Hosted CI | Run on GitHub-hosted Ubuntu, macOS and Windows runners, CPython 3.11 to 3.14. See [CI](ci.md) |
| Installation | Documented commands verified against a locally built wheel, with pip confined to that wheel. Installation from PyPI is verified only after publication: see [PUBLISHING](../release/PUBLISHING.md) |
| Real planner output | One saved-response integration trial, independently reviewed. See below |
| Implementer rehearsal | Run. See the [developer rehearsal](developer-rehearsal.md) |
| External developer onboarding | **Not measured.** No external developer has tried it |
| Quickstart time targets | **Not measured.** The 120-second and 10-minute targets are for people, and none has been timed |
| SARIF ingestion | **Unverified.** GitHub refuses the upload because code scanning is not enabled on the private repository |
| Adoption, production use | None claimed |

## First planner trial

One integration trial using an authored release task. A person wrote the task, and a model answered it
once. This is one response under one configuration. **It is not an estimate of model reliability.**

| Item | Observed |
| --- | --- |
| Mode | Saved raw response through the existing LangGraph gate with dry handlers |
| Policy and request | Frozen before any output existed; `release-basic`, unchanged |
| Model that produced the response | `claude-opus-5-5`, through Claude Code print mode |
| Result | `pass`, no diagnostics, no conversion: the response was strict JSON |
| Handlers | Five dry handlers, matching the plan's steps. No real action ran |
| Independent review | Archive and record manifests matched; the saved response passed SDK and CLI checks; replay dispatched exactly five dry handlers; two reviewer-authored mutations were rejected with zero handler calls |

**Counts.** One CLI invocation, one returned planner response, and an additional reported
`claude-haiku-5-5` usage entry (3,231 input and 24 output tokens). This must not be described as exactly one
model call. The additional usage exceeded the one request that was authorized. Its purpose and content
remain unknown.

**Isolation limitations.**

- The model that answered is the same model that ran the trial, in a separate process with no shared
  conversation and no tools.
- An empty system prompt was requested. The text the runtime finally sent was not observable.
- The process inherited the parent session's environment, including the marker for a nested session.
- Temperature, seed, output limit and model date are unknown.

The bundled failing plans are authored. In this trial the model's plan passed, so the demo's failure is
not a naturally occurring model failure.

## Not established by any of this

Whether developers can use it unaided, how often any model produces a violating plan, or how the tool
behaves in production. Hosted CI and the rehearsal show that the software behaves as tested.
