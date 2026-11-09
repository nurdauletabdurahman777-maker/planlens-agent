# planlens-agent

**Lint AI-agent plans before they run.**

For developers building structured plan-and-execute agents.

| You own | Your agent proposes | You get |
| --- | --- | --- |
| An action catalog and a workflow contract | A sequential plan | A deterministic verdict, specific diagnostics and an optional execution gate |

![Terminal demo. planlens-agent checks a bundled plan and rejects it with plan/artifact-identity because deploy_prod references build_hotfix.artifact while tests and the scan use build_rc.artifact. A copy is corrected to deploy build_rc.artifact, and the recheck passes with exit 0.](docs/media/demo.gif)

*An authored example, rendered from real installed-wheel output. Nothing was deployed. [Static version](docs/media/demo.png).*

Every required action is in this plan, and it is still wrong: the deployment references a different
build from the one that was tested and scanned.

**Install → Check → Understand → Correct → Recheck → Integrate**

> **Private pre-release.** The [repository](https://github.com/nurdauletabdurahman777-maker/planlens-agent)
> is private, so you need read access to it. It is not on PyPI. After installation it runs offline, with
> no model account and no telemetry.

## Install

You need Python 3.11 or newer (check `python3 --version`), Git, and read access to the repository. Building
the wheel downloads its pinned build backend once. This is the only step here that needs a network.
[Troubleshooting](docs/quickstart.md#troubleshooting).

<!-- run prereq -->
```sh
git clone --branch launch/developer-experience https://github.com/nurdauletabdurahman777-maker/planlens-agent.git
python3 -m venv build-env
build-env/bin/python -m pip wheel --no-deps --wheel-dir wheelhouse ./planlens-agent
```

Install it, with no network needed:

<!-- run contains="planlens-agent 0.1.0.dev0" -->
```sh
python3 -m venv planlens-env
. planlens-env/bin/activate
pip install --no-index wheelhouse/planlens_agent-0.1.0.dev0-py3-none-any.whl
planlens-agent --version
```

## Check

`init` copies an authored example: a catalog, a contract and some candidate plans.

<!-- run exit=1 contains="RESULT: REJECTED (exit 1) - 1 violation" contains="plan/artifact-identity" -->
```sh
planlens-agent init release-basic demo
cd demo
planlens-agent check plans/artifact-mismatch.json
```

Exit status 0 means pass, 1 rejected, and 2 invalid input or unsupported requirements.

## Understand

The finding names the step, what was observed, what was required, and what to change. For the same
result as a report, with the steps and artifact references it involves:

<!-- run exit=1 contains="wrote report.html: REJECTED (exit 1), 1 finding" -->
```sh
planlens-agent inspect plans/artifact-mismatch.json --output report.html
```

Open `report.html` in a browser. It is one self-contained file and loads nothing from the network.

![PlanLens inspector report for the authored artifact-mismatch plan. A rejected verdict appears at the top. The finding lists plan/artifact-identity with its observed references, requirement and next step. The proposed sequence marks test_rc and scan_rc as using build_rc.artifact and deploy_prod as using build_hotfix.artifact.](docs/media/inspector.png)

## Correct

Change a copy, so the bundled plan stays as it is. Here the deployment uses the build that was tested
and scanned:

<!-- run -->
```sh
sed 's/build_hotfix.artifact/build_rc.artifact/' plans/artifact-mismatch.json > my-plan.json
```

## Recheck

<!-- run contains="RESULT: PASS (exit 0)" -->
```sh
planlens-agent check my-plan.json
```

A pass means the **declared plan** satisfies your supported, required rules. It does not show that any step
ran, that tests passed, or what was deployed. Requirements the engine cannot evaluate are never silently
passed: they end in exit 2.

## Integrate

| Where | What | Guide |
| --- | --- | --- |
| A first workflow of your own | Three files, one rule | [Quickstart](docs/quickstart.md#6-describe-your-own-workflow) |
| CLI and CI | Exit statuses, JSON, SARIF, suites | [CI](docs/ci.md), [Comparing runs](docs/suites.md) |
| Python | Check, then dispatch only what passed | [Integration](docs/integration.md) |
| LangGraph | Planner, check, then execute or reject | [Recipe](examples/langgraph_gate/README.md) |

Partial snippet: `agent_plan` and `handlers` are yours.

```python
from planlens_agent import ReferenceRunner, check

result = check(agent_plan, "contract.toml", "catalog.toml")  # a dict or a path
if result.passed:
    runner = ReferenceRunner("contract.toml", "catalog.toml", handlers)
    runner.dispatch(result.approve(), payload=agent_plan)  # runs the exact snapshot that passed
```

This is a library: code that never calls it is not constrained by it.

## Status

| | |
| --- | --- |
| Hosted checks | Run on Ubuntu, macOS and Windows, on CPython 3.11 to 3.14 |
| Real planner output | One saved-response integration trial, independently reviewed. It is one response to an authored task, not a reliability estimate |
| External developer onboarding | Not measured. No external developer has tried it |
| SARIF ingestion | Unverified: code scanning is not enabled on the private repository |

Details and limits: [Evidence](docs/evidence.md). The names are provisional: [Naming](docs/naming.md).

## Documentation

- [Quickstart](docs/quickstart.md): the whole journey, including your own workflow
- [Semantics](docs/semantics.md) and [rules](docs/rules.md): what is checked, and every rule
- [Integration](docs/integration.md), [CI](docs/ci.md), [Comparing runs](docs/suites.md)
- [Inspector](docs/inspector.md) and [SARIF](docs/sarif.md)
- [Evidence](docs/evidence.md) and the [developer rehearsal](docs/developer-rehearsal.md)
- [Provenance](PROVENANCE.md) and [changelog](CHANGELOG.md)

## Development

```console
uv venv && uv pip install -e ".[dev]"
python -m pytest
python scripts/ci_local.py all --python 3.14
uv run --script scripts/make_demo_media.py
```

The last command regenerates `docs/media`. Pillow is used only by that script.

Licensed under the Apache License 2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE). Authorship and
copyright holders are pending; see [AUTHORS](AUTHORS.md). The OASIS SARIF schema in `tests/data/sarif/` is
a separately licensed test resource, outside Apache-2.0 and outside the wheel.
