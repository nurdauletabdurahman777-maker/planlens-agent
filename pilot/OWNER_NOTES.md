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
| Task 2: first bundled check, after install | 120 seconds | project standard, section 4 |
| Task 1: end-to-end install | none; record it separately | project standard, section 4 |
| Task 3: inspect and identify a correction | **not set** | the standard defines none |
| Task 4: own workflow | **not set**; at least one tester must finish | project standard, section 12 |

The standard has no time target for Tasks 3 and 4. If you want one, write it here before the first
session. Do not set or change a target after seeing results. The 15 and 45 minute limits in the
tester guide are session caps, not targets.

## Recording results

- Fill in the table from returned forms only. An empty row stays `not run`.
- A missed target stays missed. Do not drop a run, re-time it or average it away.
- An unfinished task is reported as unfinished, with the limit it reached. It is not a time.
- Preserve failures, then fix them, then say which package hash each fix applies to.
- Report the correction as right only if the tester's own change passed on the first try, and the
  sentence they wrote matches it.
- Keep usefulness answers verbatim. Do not turn them into adoption or satisfaction claims.

| tester | package SHA-256 | T1 install | T2 first check | T2 met 120 s? | T3 inspect | T3 right first try? | T4 own workflow | T4 finished? | blockers |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | not run | | | | | | | | |
| B | not run | | | | | | | | |
| C | not run | | | | | | | | |
