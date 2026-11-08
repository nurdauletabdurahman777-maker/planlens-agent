"""Walk the developer journey against an installed command, asserting every outcome.

    python scripts/journey.py --cli PATH --work DIR [--log FILE]
                              [--gate-python PATH --gate-script PATH]

Stdlib only. Everything happens in DIR, using what `planlens-agent init` creates, so this
exercises the installed package rather than the checkout. Every plan is an authored
example. Exits 1 at the end if any expectation was not met.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


class Journey:
    def __init__(self, cli: str, work: Path, log: Path | None) -> None:
        self.cli, self.work, self.log_path = cli, work, log
        self.lines: list[str] = []
        self.failures: list[str] = []

    def say(self, text: str = "") -> None:
        text = text.replace(str(self.work), "<work>")
        print(text)
        self.lines.append(text)

    def title(self, text: str) -> None:
        self.say()
        self.say(f"=== {text}")

    def expect(self, condition: bool, what: str) -> None:
        self.say(f"{'ok' if condition else 'FAILED'}: {what}")
        if not condition:
            self.failures.append(what)

    def exec(self, argv: list[str], shown: str, expected: int, cwd: Path) -> str:
        self.say(f"$ {shown}")
        done = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", cwd=cwd, check=False
        )
        for text in (done.stdout, done.stderr):
            if text.strip():
                self.say(text.rstrip())
        self.expect(
            done.returncode == expected, f"exit status {done.returncode} (expected {expected})"
        )
        return done.stdout + done.stderr

    def run(self, *args: str, expected: int, cwd: Path) -> str:
        return self.exec([self.cli, *args], "planlens-agent " + " ".join(args), expected, cwd)


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def named(entries: list[dict[str, Any]]) -> list[str]:
    return [e["case_id"] for e in entries]


def journey(j: Journey, gate_python: str | None, gate_script: str | None) -> None:
    j.title("setup: an authored example project")
    j.run("--version", expected=0, cwd=j.work)
    j.run("init", "release-basic", "project", expected=0, cwd=j.work)
    p = j.work / "project"

    j.title("1. passing suite")
    j.run(
        "suite", "run", "suite.toml", "--plans-dir", "candidates/v3",
        "--output", "v3.run.json", expected=0, cwd=p,
    )  # fmt: skip
    j.expect(load(p / "v3.run.json")["status"] == "passed", "run status is passed")

    j.title("2. rejected suite")
    j.run("suite", "run", "suite.toml", "--label", "planner=example-a",
          "--output", "v1.run.json", expected=1, cwd=p)  # fmt: skip
    j.expect(load(p / "v1.run.json")["summary"]["rejected"] == 2, "two cases rejected")

    j.title("3. incomplete suite with a structured error")
    shutil.copytree(p / "candidates/v3", p / "broken")
    (p / "broken/legacy.json").write_text("{not json", encoding="utf-8")
    j.run("suite", "run", "suite.toml", "--plans-dir", "broken",
          "--output", "broken.run.json", expected=2, cwd=p)  # fmt: skip
    run = load(p / "broken.run.json")
    bad = [c for c in run["cases"] if c["verdict"] == "error"]
    j.expect(run["status"] == "incomplete", "run status is incomplete")
    j.expect(named(bad) == ["legacy"], "only the broken case is an error")
    j.expect(bad[0]["findings"][0]["rule_id"] == "plan/malformed", "the error is structured")
    j.expect(run["summary"]["passed"] == 4, "the four valid results are kept")

    j.title("4-7. new, resolved, unchanged and index-only movement")
    j.run("suite", "run", "suite.toml", "--plans-dir", "candidates/v2",
          "--label", "planner=example-b", "--output", "v2.run.json", expected=1, cwd=p)  # fmt: skip
    j.run("suite", "diff", "v1.run.json", "v2.run.json", "--output", "v1-v2.diff.json",
          expected=1, cwd=p)  # fmt: skip
    d = load(p / "v1-v2.diff.json")
    j.expect(named(d["findings"]["new"]) == ["expedite"], "4. expedite is a new violation")
    j.expect(named(d["findings"]["resolved"]) == ["hotfix"], "5. hotfix is resolved")
    unchanged = d["findings"]["unchanged"]
    j.expect(named(unchanged) == ["legacy"], "6. legacy is unchanged")
    j.expect(
        unchanged[0]["moved"] and unchanged[0]["baseline_position"] != unchanged[0]["position"],
        "7. legacy moved position and is still one unchanged finding",
    )

    j.title("8a. a missing case is not an improvement")
    text = (p / "suite.toml").read_text(encoding="utf-8")
    hotfix = '[[cases]]\nid = "hotfix"\nplan = "hotfix.json"\n\n'
    if hotfix not in text:
        j.expect(False, "suite.toml has the expected hotfix case")
    (p / "dropped.toml").write_text(text.replace(hotfix, ""), encoding="utf-8")
    j.run("suite", "run", "dropped.toml", "--output", "dropped.run.json", expected=1, cwd=p)
    j.run("suite", "diff", "v1.run.json", "dropped.run.json", "--output", "dropped.diff.json",
          expected=2, cwd=p)  # fmt: skip
    d = load(p / "dropped.diff.json")
    j.expect(named(d["cases"]["removed"]) == ["hotfix"], "the dropped case is reported")
    j.expect(d["summary"]["resolved"] == 0, "its finding is not counted as resolved")

    j.title("8b. a duplicate case id")
    (p / "duplicate.toml").write_text(text + "\n" + hotfix, encoding="utf-8")
    j.run("suite", "run", "duplicate.toml", "--output", "duplicate.run.json", expected=2, cwd=p)
    run = load(p / "duplicate.run.json")
    j.expect([e["code"] for e in run["suite_errors"]] == ["duplicate-case"], "reported as such")
    j.expect("hotfix" not in named(run["cases"]), "neither duplicate entry was evaluated")

    j.title("9a. a changed policy is not attributed to the plans")
    other = j.work / "changed-policy"
    shutil.copytree(p, other)
    contract = other / "contract.toml"
    old = 'version = "1.0.0"\ndescription'
    source = contract.read_text(encoding="utf-8")
    j.expect(old in source, "contract.toml has the expected version line")
    contract.write_text(source.replace(old, 'version = "1.1.0"\ndescription'), encoding="utf-8")
    j.run("suite", "run", "suite.toml", "--plans-dir", "candidates/v3",
          "--output", "policy.run.json", expected=0, cwd=other)  # fmt: skip
    shutil.copy(other / "policy.run.json", p / "policy.run.json")
    j.run("suite", "diff", "v1.run.json", "policy.run.json", "--output", "policy.diff.json",
          expected=2, cwd=p)  # fmt: skip
    d = load(p / "policy.diff.json")
    j.expect(d["status"] == "not_comparable", "status is not_comparable")
    j.expect([c["kind"] for c in d["context_changes"]] == ["contract"], "the contract is named")

    j.title("9b. a different evaluator build is not attributed to the plans")
    run = load(p / "v1.run.json")
    run["engine"]["evaluator_sha256"] = "0" * 64  # stands in for another build
    (p / "other-build.run.json").write_text(json.dumps(run), encoding="utf-8")
    j.run("suite", "diff", "v1.run.json", "other-build.run.json", "--output", "build.diff.json",
          expected=2, cwd=p)  # fmt: skip
    d = load(p / "build.diff.json")
    j.expect([c["kind"] for c in d["context_changes"]] == ["evaluator"], "the evaluator is named")

    j.title("10. no regression, while the current suite still fails")
    j.run("suite", "run", "suite.toml", "--label", "planner=example-c",
          "--output", "v1-again.run.json", expected=1, cwd=p)  # fmt: skip
    shown = j.run("suite", "diff", "v1.run.json", "v1-again.run.json",
                  "--output", "same.diff.json", expected=0, cwd=p)  # fmt: skip
    d = load(p / "same.diff.json")
    j.expect(d["no_new_violations"] is True, "no new violations")
    j.expect(d["candidate_passes"] is False, "the candidate still does not pass")
    j.expect(d["approval"] is False, "the comparison is not an approval")
    j.expect(d["replay"] is True, "identical plans are reported as a replay")
    j.expect("not an approval" in shown, "the text says so")
    j.expect("says nothing about a new planner" in shown, "whatever the labels say")

    j.title("baselines are not overwritten")
    before = hashlib.sha256((p / "v1.run.json").read_bytes()).hexdigest()
    j.run("suite", "run", "suite.toml", "--plans-dir", "candidates/v3",
          "--output", "v1.run.json", expected=2, cwd=p)  # fmt: skip
    after = hashlib.sha256((p / "v1.run.json").read_bytes()).hexdigest()
    j.expect(before == after, "the existing run is byte-identical")

    j.title("13. a malformed saved run is invalid input, not a crash")
    run = load(p / "v1.run.json")
    run["cases"][0]["verdict"] = []
    (p / "malformed.run.json").write_text(json.dumps(run), encoding="utf-8")
    shown = j.run("suite", "diff", "v1.run.json", "malformed.run.json", expected=2, cwd=p)
    j.expect("cases[0].verdict is invalid" in shown, "the error names the field")
    j.expect("internal error" not in shown, "it is not reported as a bug")

    j.title("14. two runs agreeing on an unknown report schema are not comparable")
    for name in ("v1", "v1-again"):
        run = load(p / f"{name}.run.json")
        run["report_schema"] = "unknown-future-schema"
        (p / f"future-{name}.run.json").write_text(json.dumps(run), encoding="utf-8")
    j.run("suite", "diff", "future-v1.run.json", "future-v1-again.run.json",
          "--output", "future.diff.json", expected=2, cwd=p)  # fmt: skip
    d = load(p / "future.diff.json")
    j.expect(d["status"] == "not_comparable", "status is not_comparable")
    j.expect([c["kind"] for c in d["context_changes"]] == ["report_schema"], "the schema is named")

    j.title("15. a comparison cannot replace the runs it compares")
    for target in ("v1.run.json", "./v2.run.json"):
        name = target.removeprefix("./")
        before = hashlib.sha256((p / name).read_bytes()).hexdigest()
        j.run("suite", "diff", "v1.run.json", "v2.run.json", "--output", target,
              expected=2, cwd=p)  # fmt: skip
        after = hashlib.sha256((p / name).read_bytes()).hexdigest()
        j.expect(before == after, f"{name} is byte-identical")

    j.title("16. no report can replace the files it was made from")
    collisions = [
        ("plans/valid.json", ("check", "plans/valid.json", "--output", "plans/valid.json")),
        ("contract.toml", ("check", "plans/valid.json", "--format", "sarif",
                           "--output", "contract.toml")),
        ("plans/valid.json", ("inspect", "plans/valid.json", "--output", "plans/valid.json")),
        ("candidates/v1/hotfix.json", ("suite", "run", "suite.toml", "--overwrite",
                                       "--output", "candidates/v1/hotfix.json")),
        ("suite.toml", ("suite", "run", "suite.toml", "--overwrite", "--output", "suite.toml")),
    ]  # fmt: skip
    for target, args in collisions:
        before = hashlib.sha256((p / target).read_bytes()).hexdigest()
        j.run(*args, expected=2, cwd=p)
        after = hashlib.sha256((p / target).read_bytes()).hexdigest()
        j.expect(before == after, f"{target} is byte-identical")

    if gate_python and gate_script:
        j.title("11. framework gate: a valid candidate executes")
        shown = j.exec([gate_python, gate_script, "plans/valid.json"],
                       "python gate.py plans/valid.json", 0, p)  # fmt: skip
        j.expect("status:        executed" in shown, "the graph took the execute path")
        j.expect("handler calls: 6" in shown, "all six handlers ran")
        j.title("12. framework gate: a rejected candidate calls no handler")
        shown = j.exec([gate_python, gate_script, "plans/artifact-mismatch.json"],
                       "python gate.py plans/artifact-mismatch.json", 1, p)  # fmt: skip
        j.expect("status:        rejected" in shown, "the graph took the reject path")
        j.expect("handler calls: 0" in shown, "no handler ran")
    else:
        j.title("11-12. framework gate: not run here; the recipe stage covers it")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", required=True)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--gate-python")
    parser.add_argument("--gate-script")
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    j = Journey(args.cli, args.work.resolve(), args.log)
    try:
        journey(j, args.gate_python, args.gate_script)
    finally:
        j.say()
        j.say(f"journey: {len(j.failures)} unmet expectation(s)")
        if args.log:
            args.log.write_text("\n".join(j.lines) + "\n", encoding="utf-8")
    return 1 if j.failures else 0


if __name__ == "__main__":
    sys.exit(main())
