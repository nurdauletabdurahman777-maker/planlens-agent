# Real-planner trial

Prepared, not run. **No planner has been selected or authorized for external calls.** This folder
is for the pilot owner; testers do not need it.

`capture_trial.py` sends one planner output through the existing LangGraph gate and keeps a record
of it. The tool never calls a model itself, and a planner runs only if you pass `--planner`. Handlers
are dry: they record calls, so no real action runs.

## What the owner must supply

| input | needed | notes |
| --- | --- | --- |
| **A planner, or one saved response** | yes: this is the missing input | `--planner FILE.py:function`, or `--raw-response FILE` |
| Authorization for any external call | yes, if `--planner` calls a service | a decision naming the planner and who pays |
| The exact request text | yes | `--request`, stored byte for byte. If your planner has its own system prompt or few-shot examples, those are not part of this file: record them in `--planner-info` |
| Contract and catalog | yes | yours, or `release-basic` from `planlens-agent init`, written **before** the planner output is seen |
| Identity and settings | only what is actually known | `--planner-info`, from `planner_info.template.json`. Leave anything unknown as `null` |
| A conversion | only if output is not plan JSON | `--convert`; see below |

A planner callable takes the request string and returns text, ideally the response exactly as
received. Returning a parsed mapping works, but the original text is then lost, and the record says so:

```python
def planner(request: str) -> str:
    return text_of_your_planner_response  # untouched
```

The output must be a plan in the `planlens-agent/plan/v1` format, using the catalog's actions. Telling
the planner about that format is part of your request or prompt. Do not hide it.

## Run one trial

```console
python -m venv .venv && . .venv/bin/activate
pip install --no-index wheel/planlens_agent-0.1.0.dev0-py3-none-any.whl
pip install --require-hashes -r examples/langgraph_gate/requirements.lock
python planner-trial/capture_trial.py --origin real-planner \
  --request request.txt --contract contract.toml --catalog catalog.toml \
  --raw-response response.txt --planner-info planner_info.json --out trial-001
```

Use `--origin authored-example` for anything a person wrote. The origin is your declaration, and
nothing verifies it.

## What is recorded

| file | contents |
| --- | --- |
| `request.txt` | the exact request |
| `policy/` | exact copies of the contract and catalog; the gate reads these copies |
| `raw_output.txt` or `.json` | the planner's output before any conversion |
| `conversion.json`, `convert.py` | what was done to the output, with hashes, or that nothing was |
| `checked_plan.json` | the plan that was checked; absent if it never reached the checker |
| `report.json` | the check report |
| `planner.json` | identity and settings as supplied, or `not supplied` |
| `outcome.json` | status, dry-run calls, tool versions, time and declared origin |
| `manifest.json` | SHA-256 of each file above |

A record is never replaced: `--out` must be a new directory.

## Conversions

By default the output must be strict JSON. Nothing is repaired, so text around the JSON, a Markdown
fence or a duplicate key ends in `error`, with the raw output kept. That is a finding about the
planner's output, so keep it.

If you decide a conversion is legitimate, write it as `convert(raw)` in a file and pass `--convert`.
The script and hashes go in the record. Report the result as "after this conversion", and never edit
the raw output by hand. Do not strip fields the checker would refuse.

## What a trial can and cannot show

One trial shows what happened to one output, under one configuration. It does not measure a model's
failure rate. Report rejections, passes and conversion failures alike. Do not retry until one looks
good and report only that one: keep each attempt in its own `--out` directory.

Do not put credentials in `--planner-info`: fields named like keys or tokens are refused. Check raw
outputs for sensitive content before sharing a record.
