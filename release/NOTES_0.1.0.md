PlanLens checks structured, sequential AI-agent plans against workflow rules you define, before execution.

A release plan can contain build, tests, security scan, review and deploy—and still deploy a different build from the one its tests reference. PlanLens identifies that mismatch and reports the declared requirement it violates.

### Included

- Deterministic checks for required actions, prerequisite order, typed artifact references and same-artifact requirements.
- CLI and Python SDK with structured diagnostics.
- A self-contained HTML Inspector for examining findings and involved steps.
- Suite runs and comparisons for tracking changes between candidate plans.
- Snapshot-bound approvals and a reference runner, with an optional LangGraph integration recipe.
- Text, JSON and SARIF output.

### Try it

In a Python 3.11+ virtual environment:

```sh
python -m pip install "planlens-agent==0.1.0"
planlens-agent init release-basic demo
cd demo
planlens-agent check plans/artifact-mismatch.json
```

Expect `REJECTED`, exit status `1`, with `plan/artifact-identity`. This bundled example deliberately violates its contract.

### Scope

PlanLens checks declared plans against a supplied catalog and contract. A pass does not establish that steps ran, tests passed or artifact bytes were verified. The public API may evolve during 0.x.

Python 3.11+ · Apache-2.0 · No core runtime dependencies.

### Documentation

- [Quickstart](https://github.com/nurdauletabdurahman777-maker/planlens-agent/blob/v0.1.0/docs/quickstart.md)
- [Inspector](https://github.com/nurdauletabdurahman777-maker/planlens-agent/blob/v0.1.0/docs/inspector.md)
- [Evidence and limits](https://github.com/nurdauletabdurahman777-maker/planlens-agent/blob/v0.1.0/docs/evidence.md)
