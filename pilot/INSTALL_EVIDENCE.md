# Pilot install evidence

What the implementer observed when following the pilot instructions. This shows the instructions
work on this machine. It is **not** tester evidence and says nothing about usability. Not shipped
in the package. Every plan used is authored.

| | |
| --- | --- |
| Package | `dist/planlens-agent-private-pilot.zip` |
| SHA-256 | `624a56cb2d7b2d70266227f0200f792b46869234b530053ad79aa1996ad5400c` |
| Bytes | 180097 |
| Built from commit | `7ab3217d74132f520b56a3f4310b2a755b122af5` |
| Platform | macOS 26.6.2 arm64 only |
| Python | CPython 3.14.7 and 3.11.15 (uv-managed) |

The wheel's code matches the reviewed B2-r3 wheel file for file. Only `METADATA` and `RECORD`
differ, because `METADATA` embeds the README, which changed in the last reviewed commit.

## Method

Each run used a fresh extraction of the ZIP outside the checkout, `env -i` with `HOME` set to an
empty directory and `PATH` holding only the chosen Python and the system directories, and the
macOS default shell (`zsh -f`). Commands were extracted from `README.md` and run as written.
Sections 1 to 6 ran with network access denied by `sandbox-exec`.

## Results, identical on both Python versions

| command | exit | observed |
| --- | --- | --- |
| `python3 -m venv .venv` | 0 | |
| `. .venv/bin/activate` | 0 | |
| `pip install --no-index wheel/planlens_agent-0.1.0.dev0-py3-none-any.whl` | 0 | `Successfully installed planlens-agent-0.1.0.dev0` |
| `planlens-agent --version` | 0 | `planlens-agent 0.1.0.dev0` |
| `planlens-agent init release-basic demo` | 0 | `Created release-basic template in demo` |
| `planlens-agent validate-policy` | 0 | `OK - contract release-basic 1.0.0 is valid` |
| `planlens-agent check plans/valid.json` | 0 | `RESULT: PASS (exit 0)` |
| `planlens-agent check plans/reversed-review.json` | 1 | `RESULT: REJECTED`, `plan/prerequisite-order` |
| `planlens-agent check plans/artifact-mismatch.json` | 1 | `RESULT: REJECTED`, `plan/artifact-identity` |
| `planlens-agent inspect plans/artifact-mismatch.json --output report.html` | 1 | `wrote report.html: REJECTED (exit 1), 1 finding` |
| `python ../examples/sdk_check.py` | 0 | |
| `python ../examples/guarded_runner.py` | 0 | a rejected plan cannot be approved |
| Optional, network allowed: `pip install --require-hashes -r ../examples/langgraph_gate/requirements.lock` | 0 | |
| `python ../examples/langgraph_gate/gate.py plans/valid.json` | 0 | `executed`, 6 handler calls |
| `python ../examples/langgraph_gate/gate.py plans/artifact-mismatch.json` | 1 | `rejected`, 0 handler calls |

## Other observations

- `open report.html` was not run, because it opens a window. The report was rendered in Google Chrome
  156 headless and viewed: the verdict, finding, Next row and proposed sequence all display. No other
  browser was tried.
- Older Python, the system's 3.9.6: `pip install` exits 1 with
  `ERROR: Package 'planlens-agent' requires a different Python: 3.9.6 not in '>=3.11'`.
- Correction, applied to a copy by script: changing `deploy_prod`'s artifact to `build_rc.artifact`
  gives `RESULT: PASS`, exit 0.

## Capture rehearsal

Run from the package with `--origin authored-example`. No planner was involved, and no model was called.

| raw response | exit | status | dry-run handler calls |
| --- | --- | --- | --- |
| authored `valid.json` | 0 | `executed` | 6 |
| authored `artifact-mismatch.json` | 1 | `rejected`, `plan/artifact-identity` | 0 |
| authored prose, not JSON | 2 | `error`, raw output kept | 0 |

## First-trial files

`planner-trial/first-trial/` holds the frozen request and policy. No model has been called. The exact
capture command from its README was run once with an authored stand-in response and
`--origin authored-example` in place of `--origin real-planner`: exit 1, `rejected`,
`plan/prerequisite-order`.

## Not tried

Linux and Windows for these instructions, any person other than the implementer, and any real planner.
