# Examples

Every plan and handler here is authored. No example calls a model or performs a real action.

| Example | What it shows |
| --- | --- |
| [`sdk_check.py`](sdk_check.py) | Check plans from Python, with the same result the command gives |
| [`guarded_runner.py`](guarded_runner.py) | Dispatch only a plan that passed, and only the exact snapshot that passed |
| [`langgraph_gate/`](langgraph_gate/README.md) | Planner, check, then execute or reject, in a real LangGraph graph |

`planlens-agent init release-basic` creates the bundled catalog, contract, plans and suite these use. Run the
Python examples from that folder. The [Quickstart](../docs/quickstart.md#7-integrate) has the exact commands.
