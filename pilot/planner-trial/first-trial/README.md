# First planner trial

A **single integration trial using an authored release task**. A person wrote the task. If a model
then answers it, that answer is real planner output, but one response under one configuration. It is
not a measurement of any model.

Status: **prepared, not run.** No model has been called and no output has been received.

## Owner decisions

- Saved raw-response mode: the capture tool calls nothing.
- `release-basic` catalog and contract, unchanged from reviewed commit
  `7cceea81da4cde6b942038a8490210f1212b54a8`.
- Identity and sampling fields stay `null` unless actually known.

## Frozen files

`request.txt`, `contract.toml` and `catalog.toml` were fixed before any output was received, and
their hashes are in `FROZEN.sha256`. Do not edit them. A changed request or policy is a different
trial. `request.txt` contains only the task and the actual plan schema, catalog and contract: no
example plan and nothing intended to induce failure.

## Procedure

From the unzipped pilot package, with the setup in `planner-trial/README.md` done:

1. Confirm the freeze:

   ```console
   (cd planner-trial/first-trial && shasum -a 256 -c FROZEN.sha256)
   ```

2. Open a fresh conversation with no custom instructions. Paste the whole of `request.txt` as one
   message, and add nothing.
3. Save the complete reply to `response.txt` **before judging it**, exactly as it arrived, including
   any Markdown fences or commentary. Do not edit it. A chat interface's copy button can change a
   reply, for example by dropping fences, so prefer its raw or source view.
4. If you actually know the model or settings, fill in only those fields of
   `planner-trial/first-trial/planner_info.json`, and say how you know in
   `how_this_information_is_known`. Leave everything else `null`.
5. Check the saved response:

   ```console
   python planner-trial/capture_trial.py --origin real-planner \
     --label "single integration trial using an authored release task" \
     --request planner-trial/first-trial/request.txt \
     --contract planner-trial/first-trial/contract.toml \
     --catalog planner-trial/first-trial/catalog.toml \
     --planner-info planner-trial/first-trial/planner_info.json \
     --raw-response response.txt \
     --out trial-001
   ```

   The exit status is 0 if the plan passes, 1 if rejected, and 2 if it could not be evaluated.

## Keep the result either way

Preserve `response.txt` and `trial-001/` whether the plan passes, is rejected or cannot be parsed.
If the response is not strict JSON, that is the result of this trial. Do not repair it. Any later
attempt or conversion is a separate, separately labelled record.

`--origin real-planner` is your declaration, and nothing verifies it. A pass here shows that this one
declared plan met these rules. It does not show that the plan ran or that the model is reliable.
