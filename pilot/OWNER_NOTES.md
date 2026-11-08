# Pilot owner notes

Not for testers: the targets below could bias their timing. No testers have been contacted, and
no session has been run.

## Who and how

Three developers who did not implement PlanLens and have not seen this code. Authors of the product
or the research artifact do not count. Use tester codes, not names. Tell testers what you will keep,
and keep only what they agree to. Three runs are initial onboarding evidence. They are not
statistical usability evidence or evidence of production reliability.

No live coaching. Do not answer questions during timed tasks: note them instead. Send every tester
the same ZIP, identified by its SHA-256. If you change the package between sessions, record which
tester had which hash, because their results are then not directly comparable.

## Targets

| measure | target | source |
| --- | --- | --- |
| Task 1: installation | none; recorded separately | Standard section 4 |
| Task 2: first bundled check, after installation | 120 seconds | Standard section 4 |
| Task 1 + Task 2: end-to-end to first check, including prerequisite installation | none; recorded | owner decision |
| Task 3: inspect and identify a correction | none; 15-minute session cap | owner decision |
| Task 4: first contract for a deliberately small workflow of the tester's own | 10 minutes | Addendum section 16.1 |

Installation does not count against the 120 seconds, and neither limit below is a target.

- The 45-minute Task 4 limit and the 15-minute Task 3 limit are session caps. Finishing inside a cap
  does not meet a target.
- Task 4's small scenario is three or four actions and one ordering rule. It starts when the tester
  opens their first own file, after choosing the workflow, and stops when `validate-policy` exits 0
  and `check` gives the verdict they predicted.
- These targets stay targets until measured in non-author runs. Do not change one after seeing
  results.

## Recording results

- Fill in the table from returned forms only. An empty row stays `not run`.
- A missed target stays missed. Do not drop a run, re-time it or average it away, and do not
  present a missed target as successful onboarding. Report it as, for example, "2 of 3 met the
  120-second target".
- If a tester misses the 10-minute target, record the actual time and the blocker, and fix the blocker
  before any public launch rather than adjusting the target (Addendum section 16.4).
- An unfinished task is reported as unfinished, with the limit it reached. It is not a time.
- Preserve failures, then fix them, then say which package hash each fix applies to.
- Report the correction as right only if the tester's own change passed on the first try, and the
  sentence they wrote matches it.
- Keep usefulness answers verbatim. Do not turn them into adoption or satisfaction claims.

| tester | package SHA-256 | T1 install | Python installed? | T2 first check | T2 met 120 s? | T1 + T2 | T3 inspect | T3 right first try? | T4 own workflow | T4 finished in cap? | T4 met 10 min? | blockers |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | not run | | | | | | | | | | | |
| B | not run | | | | | | | | | | | |
| C | not run | | | | | | | | | | | |
