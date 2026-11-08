# planlens-agent private pilot

`planlens-agent` checks an AI agent's structured plan against workflow rules you own, before
anything runs, and says specifically what is wrong.

This is a private pre-release. Please do not redistribute it. Every plan in this package is an
**authored example**, not output from any model. If you were asked to time your first run, follow
`TESTER_GUIDE.md`, and use this page as the reference.

## What you need

- Python 3.11 or newer. Check with `python3 --version`. An older Python cannot install the wheel.
- A terminal on macOS or Linux. On Windows, the activation command is `.venv\Scripts\activate` and
  you open files with `start`; these exact steps have not been followed on Windows.
- A web browser, for section 5.
- No account, API key, paid service or repository checkout. Sections 1 to 6 need no network.

Type what follows each `$`, from the folder this file is in. Lines under a command show what to
look for, and `[exit N]` is its exit status (see it with `echo $?`).

## 1. Install

```console
$ python3 -m venv .venv
$ . .venv/bin/activate
$ pip install --no-index wheel/planlens_agent-0.1.0.dev0-py3-none-any.whl
Successfully installed planlens-agent-0.1.0.dev0
$ planlens-agent --version
planlens-agent 0.1.0.dev0
$ planlens-agent init release-basic demo
Created release-basic template in demo
$ cd demo
```

`demo/` holds authored example files: a `catalog.toml` of allowed actions and a `contract.toml` of
rules, both owned by you, and candidate `plans/`.

## 2. A passing plan

```console
$ planlens-agent validate-policy
OK - contract release-basic 1.0.0 is valid
[exit 0]
$ planlens-agent check plans/valid.json
RESULT: PASS (exit 0)
[exit 0]
```

A pass means the declared plan satisfies your rules. It does not show that any step ran.

## 3. A dependency violation

```console
$ planlens-agent check plans/reversed-review.json
RESULT: REJECTED (exit 1) - 1 violation
plan/prerequisite-order
[exit 1]
```

Every required step is present, but the deploy comes before the review. The output names the step,
what was observed, what was required, and what to change next.

## 4. An artifact mismatch

```console
$ planlens-agent check plans/artifact-mismatch.json
RESULT: REJECTED (exit 1) - 1 violation
plan/artifact-identity
[exit 1]
```

Here the order is fine, but the deploy does not use the build that was tested and scanned.

## 5. Open the inspector

```console
$ planlens-agent inspect plans/artifact-mismatch.json --output report.html
wrote report.html: REJECTED (exit 1), 1 finding
[exit 1]
$ open report.html
```

On Linux use `xdg-open report.html`. The report is one HTML file and loads nothing from the network.

Start with the verdict at the top right, then the finding on the left: its message, what was
observed and required, and a **Next** row saying what to change. The proposed sequence shows the
steps involved. Select a finding to highlight them. To try a change, copy the plan to `my-plan.json`, edit the copy, and run
`planlens-agent check my-plan.json`. Leave `plans/` as it is.

## 6. Use it from Python

```console
$ python ../examples/sdk_check.py
[exit 0]
$ python ../examples/guarded_runner.py
a rejected plan cannot be approved, so nothing runs
[exit 0]
```

## Optional: the LangGraph gate

This one step downloads pinned packages, so it needs network access. It runs a real LangGraph
graph with an authored plan in place of a planner.

```console
$ pip install --require-hashes -r ../examples/langgraph_gate/requirements.lock
$ python ../examples/langgraph_gate/gate.py plans/valid.json
status:        executed
handler calls: 6
[exit 0]
$ python ../examples/langgraph_gate/gate.py plans/artifact-mismatch.json
status:        rejected
handler calls: 0
[exit 1]
```

## Limits

It checks one declared, ordered, sequential plan. It does not run your actions, see what actually
happened, or check parallel graphs and tool traces. Nothing is sent anywhere: the tool has no
telemetry. Authorship and copyright details in `NOTICE` and `AUTHORS.md` are still pending.
