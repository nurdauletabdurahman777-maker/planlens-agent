# LangGraph gate

planner → PlanLens check → execute or reject, as a real LangGraph graph.

Tested with **LangGraph 1.2.20**, the version `uv` resolved on 2026-11-08, on the Python
versions listed in [docs/ci.md](../../docs/ci.md). `requirements.lock` holds the full hashed
set. This is the only version claimed; no compatibility range is implied.

LangGraph is a dependency of this recipe only. `planlens-agent` itself has none.

## Run it

```console
python -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements.lock
pip install --no-deps "planlens-agent==0.1.0"
planlens-agent init release-basic demo && cd demo
python ../gate.py plans/valid.json              # exit 0, 6 handler calls
python ../gate.py plans/artifact-mismatch.json  # exit 1, 0 handler calls
```

The planner in that run is `FixturePlanner`. It returns an authored file. It is not a model,
and these runs show how the gate behaves, not how any model behaves.

## Connect a planner

A planner is any callable that takes the request and returns the candidate as a mapping:

```python
def planner(request: str) -> Mapping[str, Any]:
    return json.loads(your_model_call(request))  # untrusted candidate


gate = build_gate(planner, "contract.toml", "catalog.toml", handlers)
state = gate.invoke({"request": "release 2.4.0"})
```

## What the graph guarantees

| outcome | `status` | handlers |
| --- | --- | --- |
| plan passes | `executed` | run in plan order, from the approved snapshot |
| plan rejected | `rejected` | none |
| plan invalid, unsupported or not evaluated | `error` | none |
| planner raised, or returned something other than a mapping | `error` | none |
| plan in the state changed or removed after the check | `blocked` | none |
| a handler raised | `handler_failed` | stops at that step |

- The contract and catalog come from `build_gate`. Nothing in the graph state replaces them.
- A caller supplies only `request`. Every invocation starts clean, so an approval, plan or
  status from an earlier run, including a returned state passed back in, authorizes nothing.
- The plan reaches `check()` as it arrived. Fields are never removed first, so a field the
  checker refuses is still refused.
- A string from a planner never reaches `check()`, which would read it as a file path.
- Execution goes through `ReferenceRunner`, which runs its approved snapshot and compares the
  plan in the state against it.

## What it does not cover

- It gates one ordered, sequential plan. It does not check arbitrary LangGraph workflows,
  parallel graphs or tool traces, and it does not check the graph you embed it in.
- Handlers are yours. The check covers the plan, not what the code behind an action does.
- It compiles without a checkpointer. An approval is an in-memory object: after resuming from
  saved state, check the plan again.
- Code that calls your tools outside this graph is not gated by it.
- Nodes in your own graph are trusted code. One that replaces both the approval and the plan
  with another passing pair is not detected; the runner still re-checks that plan against the
  contract before running it.
- LangGraph's own tracing is controlled by your environment. The test confirms no connection
  is made with tracing unset. If you enable LangSmith tracing, plans may leave your machine.
