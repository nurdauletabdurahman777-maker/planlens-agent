# Inspector

`planlens-agent inspect` writes one self-contained HTML file describing one
recorded check.

```console
planlens-agent inspect plans/artifact-mismatch.json --output report.html
```

It takes the same `--contract` and `--catalog` arguments as `check`, and exits
0, 1 or 2 the same way. It writes a report for invalid input too. From Python:

```python
from planlens_agent import build_view, check, render_html

html = render_html(build_view(check(plan, "contract.toml", "catalog.toml")))
```

Open the file in a browser. It needs no server, account, network or model
credentials.

## How it is built

```
CheckResult  ->  build_view()  ->  render_html()  ->  one HTML file
(diagnostics,    explicit view     all content as     + a small script that
 plan snapshot,  model, lookups    escaped HTML       highlights selections
 policy)         only
```

- **There is no second verifier.** `build_view` looks facts up; every judgement
  comes from the result's diagnostics. The bundled script only highlights what a
  finding already points at.
- **Nothing is read from wording.** What a finding focuses comes from its
  structured locations and contract-rule id, never from parsing its message.
- **It is a record.** The page does not re-evaluate anything. To change a result,
  edit the plan and run the check again, which writes a fresh report.

## What the page shows

| part | content |
| --- | --- |
| verdict | pass, rejected or error, the exit code, and a plain answer |
| identity | plan, contract, catalog (with digests) and engine version; the scope statement |
| findings | rule id and version, message, observed and required, explanation, next step, related locations |
| proposed sequence | a diagram, then a table of every step in list order |
| requirements | required actions (present or absent) and prerequisite rules, with where they fall in the plan |
| artifacts | each producer output and its consumers, plus every `same_artifact` rule's bindings |

Selecting a finding focuses the steps, relationship or artifact group it involves.
Steps that produce a compared artifact are marked separately with a dashed outline:
they are looked up to show where each artifact comes from, and are not part of the
finding itself.

**Evidence.** Each finding shows its explanation, next step and a compact summary of
the observed and required values, built from those structured fields alone. The
complete values are one click away under "Raw observed and required data", a native
`<details>` control that needs no scripting, and all related locations are listed the
same way when there are more than six. Disclosures open automatically when printing.

**`same_artifact`.** Every compared binding is listed. References that agree share a
group label, assigned in plan order: A to Z, then AA to ZZ, then AAA and so on, with no
upper limit. A label stands for one reference throughout the report, across all rules.
It means "same reference" and nothing more: group A is not the correct one, and the
page says the checker cannot tell which group is wrong.

**A missing required action** is shown as absent in the requirements table. It has no
step, no position and no diagram node.

**A plan that was not evaluated** (invalid plan, invalid policy, unsupported plan
semantics) shows the findings and no sequence, requirement or artifact view: the
inspector does not reconstruct what the checker could not use. An unsupported
contract rule still shows the plan, because the supported rules were evaluated; the
verdict is `error` and the rule is listed.

## Keyboard and fallbacks

Tab reaches the skip link, then the findings. Arrow keys, Home and End move between
findings and select them; Enter or Space toggles; Escape clears. The diagram scrolls
sideways on narrow screens and is keyboard focusable. Every fact in the diagram is
also in the tables; below 640 px the tables stack with their column labels.

With scripting off, every finding and table is still shown; only selection is lost.

The same result is available as text, JSON (`--format json`) and SARIF
(`--format sarif`).

## Safety

Every input string is treated as untrusted:

- text and attributes are HTML-escaped, and control and bidirectional-formatting
  characters are shown as visible escapes;
- the embedded view data escapes `<`, `>` and `&`, so no input can close its
  `<script>` block;
- the page sets a Content-Security-Policy of `default-src 'none'`, allowing only its
  own inline style and script by hash. It cannot load or send anything.

## Limits

- The report records paths as they were given. A path passed as an absolute path
  appears in the file, so use relative paths for a report you intend to share.
- The diagram is omitted above 150 steps or 1,500 relationships; the tables remain. The
  relationship count is taken before any of the diagram is built.
- Long step labels are shortened in the diagram only; the table shows them in full.
- Findings are listed one by one, and the page embeds its data, so a very large plan
  makes a long page and a large file: about 1.5 MB for 726 steps.
- Only the first four group labels have their own colour; the rest are grey and
  distinguished by their label.
- Rendered and checked in Chrome only. See `B1_REVIEW.md`.
