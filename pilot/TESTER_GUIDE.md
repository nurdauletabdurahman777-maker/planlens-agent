# Tester guide

Thank you for trying this. It takes about an hour at most. We are testing the tool, not you: if
something is confusing or fails, that is exactly what we need to know.

**Please work alone.** Do not ask the people who built it for help during the timed tasks. If you
get stuck, write down where, and move on when you reach the time limit. An unfinished task is a
useful result.

You need Python 3.11 or newer, a browser, and a stopwatch or phone timer. Do not put secrets or
confidential details in the form. The tool sends nothing anywhere; the only data we receive is the
form you choose to return.

## Timing rules

- Record each task's start and stop times, or the elapsed time, in `FEEDBACK_FORM.md`.
- Pause the clock only for interruptions unrelated to the task, and note them. Being stuck, reading
  docs and searching the web all count as task time.
- Before Task 1, unzip the package and open a terminal in that folder. That part is not timed.

## Task 1: install

- **Start:** when you begin reading "What you need" in `README.md`.
- **Do:** section 1 of `README.md`, down to `planlens-agent --version`.
- **Stop:** when the version number prints.
- If you had to install or upgrade Python, include that time and say so on the form.

## Task 2: first bundled check

- **Start:** immediately after Task 1 stops.
- **Do:** create the example and run `planlens-agent check plans/valid.json`.
- **Stop:** when you see the `RESULT:` line.

## Task 3: inspect a failure and identify a correction

- **Start:** when you press Enter on
  `planlens-agent inspect plans/artifact-mismatch.json --output report.html`.
- **Do:** open the report and work out what is wrong and what you would change. Please use the
  report first. Limit: 15 minutes.
- **Stop:** when you have written your correction in one sentence on the form, before you try it.

Then, untimed: copy the plan to `my-plan.json`, make your change, and run
`planlens-agent check my-plan.json`. Record whether it passed on your first try. Do not change
what you wrote above.

## Task 4: describe one small workflow of your own

Pick something small you actually do, with three to six steps and at least one rule such as "X must
happen before Y". You can copy and edit `catalog.toml`, `contract.toml` and a plan from `demo/`.
You do not need a model or agent. Write the plans by hand.

- **Start:** when you begin writing or editing your own files.
- **Do:** write a catalog, a contract, and at least one plan you expect to pass or to be rejected.
  Limit: 45 minutes.
- **Stop:** when `planlens-agent validate-policy` exits 0 **and** `planlens-agent check` on your
  plan gives the result you expected.

Pass your own files with `--contract` and `--catalog`, or run from their folder. Count how many times
you ran the tool, and note every message you did not understand.

## Afterwards

Finish `FEEDBACK_FORM.md` and return it to the person who sent you this package.
