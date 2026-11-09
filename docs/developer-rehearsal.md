# Developer rehearsal

An **implementer-run rehearsal assisted by Claude Code**, following the written instructions from an
empty folder. It shows that the documented commands work. It is not usability evidence: the person
running it built the tool, and command execution times say nothing about how long a developer would take.

| Item | Value |
| --- | --- |
| Instructions followed | `README.md`, `docs/quickstart.md` and `CONTRIBUTING.md`, every copy-and-run block, as written |
| Runner | `scripts/docs_check.py`, which executes the blocks and checks every command's exit status and the output |
| Platform | macOS 26.6.2 arm64, in `zsh`, the macOS default shell |
| Python | CPython 3.14.7 and CPython 3.11.15, separately |
| Package | `planlens-agent` 0.1.0.dev0, built with `pip wheel` from a real clone of the private repository |
| Wheel SHA-256 | `7e546a410c911035dcc4917d03c0247c46f47bbc45ea9debd262bac37703f49b`, the same from both Pythons |
| Environment | Fresh folder outside the checkout. Only clone and build used the real environment, for credentials. Everything else ran with only `HOME`, `PATH` and `TERM` set |
| Network | Denied by `sandbox-exec` for everything except the clone, the build and the documented LangGraph install |

## Commands and outcomes

Identical on both Python versions. Every plan is authored.

| # | Step | Command | Exit | Observed |
| --- | --- | --- | --- | --- |
| 1 | Build | `git clone`, then `pip wheel` | 0 | Wheel built from the clone |
| 1 | Install, offline | `pip install --no-index wheelhouse/…whl` | 0 | Installed |
| 1 | Version | `planlens-agent --version` | 0 | `planlens-agent 0.1.0.dev0` |
| 2 | Initialize | `planlens-agent init` | 0 | `release-basic/` created |
| 2 | Policy | `planlens-agent validate-policy` | 0 | `OK - contract release-basic 1.0.0 is valid` |
| 2 | First check | `planlens-agent check plans/valid.json` | 0 | `RESULT: PASS` |
| 2 | Failing check | `planlens-agent check plans/artifact-mismatch.json` | 1 | `plan/artifact-identity` |
| 3 | Inspector | `planlens-agent inspect plans/artifact-mismatch.json --output report.html` | 1 | Report written; opened in Chrome; verdict, finding, steps and references present |
| 4 | Correct and recheck | `sed … > my-plan.json`, `planlens-agent check my-plan.json` | 0 | `RESULT: PASS` |
| 5 | Own workflow | Catalog, contract and two plans from section 6 | 0 | Files created |
| 5 | Own policy | `planlens-agent validate-policy` | 0 | `OK - contract database-change 1.0.0 is valid` |
| 6 | Passing candidate | `planlens-agent check good.json` | 0 | `RESULT: PASS` |
| 6 | Ordering violation | `planlens-agent check out-of-order.json` | 1 | `plan/prerequisite-order`, `backup-before-migrate` |
| 7 | SDK | `python ../planlens-agent/examples/sdk_check.py` | 0 | Ran |
| 7 | Guarded runner | `python ../planlens-agent/examples/guarded_runner.py` | 0 | A rejected plan cannot be approved |
| 7 | LangGraph install | `pip install --require-hashes -r …/requirements.lock` | 0 | LangGraph 1.2.20 |
| 7 | Gate, valid plan | `python …/gate.py plans/valid.json` | 0 | `executed`, 6 handler calls |
| 8 | Gate, rejected plan | `python …/gate.py plans/artifact-mismatch.json` | 1 | `rejected`, **0 handler calls** |
| 9 | Development checks | `uv run --locked --extra dev pytest tests -q`, Ruff and strict mypy, from the clone's root in a clean shell | 0 | Passed |

The handlers in the LangGraph demo only record that they were called. No production action ran.

## What the rehearsal found

| Finding | Kind | Outcome |
| --- | --- | --- |
| On a machine whose `python3` is older than 3.11, the build step fails with `No matching distribution found for hatchling==1.33.0`, which does not mention Python | Onboarding defect | Documented the version check and this symptom; repeated |
| The first attempt failed because the runner's clone-and-build step used the system Python 3.9 instead of the Python under test | Runner defect | Fixed the runner; repeated from an empty folder |
| Repository sign-in was assumed | Undocumented assumption | Documented |
| `gate.py`'s demo has handlers for `release-basic` actions only. Run elsewhere, it stops with `handlers registered for actions not in the catalog` | Undocumented assumption | Documented in the quickstart. This is the example script's scope, not an engine defect |
| Re-running in a used folder stops at `init` with a clear message | Expected | Added to troubleshooting |
| The GIF lost its green on the final frame, had an empty first frame, and the example sat below it | Media and layout | Fixed after viewing the frames and the rendered README |
| The runner recorded only a block's last status, so a missing script in the middle of a block went unseen (found by independent review) | Runner defect | Each command now has its own status; `docs` CI confirms a missing script fails the real quickstart |
| Command substitution inside double quotes could hide a failure (found by independent review) | Runner defect | Refused, and escaped or single-quoted text stays literal |
| The README's development commands ran outside the environment they created (found by independent review) | Documentation defect | Replaced with `uv run`, and made a verified block run from a clean shell |

**No engine defect was found.** No change was made under `src/`.

## Not verified

- Following these instructions on Linux or Windows. The hosted `docs` job runs the blocks on Ubuntu and
  macOS with a prebuilt wheel, so it does not cover the clone step. Windows has not run them.
- The contributor guide's other commands (all CI stages, media regeneration) are too long or macOS-specific
  for the docs check. Each was run once from a clean shell in a separate clone, on macOS.
- `open report.html`, which opens a window. The report was opened in headless Chrome 156 instead. No other
  browser was tried.

## External developer sessions

| Tester | Task 1 install | Task 2 first check | Task 3 inspect | Task 4 own workflow |
| --- | --- | --- | --- | --- |
| A | not run | not run | not run | not run |
| B | not run | not run | not run | not run |
| C | not run | not run | not run | not run |

The 120-second and 10-minute targets apply to people. Neither has been measured, and neither has been met.
