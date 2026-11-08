"""Write sample inspector and SARIF reports using an installed planlens-agent.

    python scripts/make_samples.py /path/to/venv/bin/planlens-agent OUT_DIR

Runs in a fresh `planlens-agent init` project, with relative paths, so the reports
hold no local paths. Inputs are the authored template and acceptance fixtures; a
"hostile" plan and contract are added to exercise escaping. Prints each exit code.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ACCEPTANCE = Path(__file__).resolve().parents[1] / "tests" / "acceptance"

CASES = [  # name, plan, contract, expected exit
    ("valid", "plans/valid.json", "contract.toml", 0),
    ("reversed-review", "plans/reversed-review.json", "contract.toml", 1),
    ("artifact-mismatch", "plans/artifact-mismatch.json", "contract.toml", 1),
    ("compound", "plans/compound.json", "contract.toml", 1),
    ("invalid-plan", "plans/malformed.json", "contract.toml", 2),
    ("invalid-policy", "plans/valid.json", "cyclic.toml", 2),
    ("unsupported-rule", "plans/valid.json", "unsupported-rule.toml", 2),
    ("hostile", "plans/hostile.json", "hostile-contract.toml", 1),
    ("large-rejected", "plans/large.json", "large-contract.json", 1),
    ("over-budget", "plans/budget.json", "budget-contract.json", 0),
]
CATALOGS = {"large-rejected": "large-catalog.json", "over-budget": "budget-catalog.json"}


def write_large_case(project: Path) -> None:
    """703 distinct references: past ZZ, and over the diagram's step limit."""
    outputs = {f"o{i}": "artifact" for i in range(32)}
    catalog = {
        "schema": "planlens-agent/catalog/v1",
        "id": "large",
        "version": "1",
        "actions": {
            "build": {"outputs": outputs},
            "use": {"inputs": {"artifact": "artifact"}},
            "deploy": {"inputs": {"artifact": "artifact"}},
        },
    }
    contract = {
        "schema": "planlens-agent/contract/v1",
        "id": "large",
        "version": "1",
        "catalog": {"id": "large", "version": "1"},
        "required_actions": ["build", "use", "deploy"],
        "rules": [
            {"id": "same", "kind": "same_artifact", "inputs": ["use.artifact", "deploy.artifact"]}
        ],
    }
    steps: list[dict[str, object]] = [{"id": f"b{i}", "action": "build"} for i in range(22)]
    steps += [
        {"id": f"u{i}", "action": "use", "inputs": {"artifact": f"b{i // 32}.o{i % 32}"}}
        for i in range(703)
    ]
    steps.append({"id": "d", "action": "deploy", "inputs": {"artifact": "b0.o0"}})
    plan = {
        "schema": "planlens-agent/plan/v1",
        "title": "Authored: 703 distinct references",
        "steps": steps,
    }
    (project / "large-catalog.json").write_text(json.dumps(catalog))
    (project / "large-contract.json").write_text(json.dumps(contract))
    (project / "plans" / "large.json").write_text(json.dumps(plan))


def write_budget_case(project: Path) -> None:
    """40 reviews then 40 deploys: passes, with 1,600 relationships to draw."""
    catalog = {
        "schema": "planlens-agent/catalog/v1",
        "id": "budget",
        "version": "1",
        "actions": {"code_review": {}, "deploy": {}},
    }
    contract = {
        "schema": "planlens-agent/contract/v1",
        "id": "budget",
        "version": "1",
        "catalog": {"id": "budget", "version": "1"},
        "required_actions": ["code_review", "deploy"],
        "rules": [
            {
                "id": "review-first",
                "kind": "prerequisite",
                "action": "deploy",
                "requires_prior": "code_review",
            }
        ],
    }
    steps = [{"id": f"r{i}", "action": "code_review"} for i in range(40)]
    steps += [{"id": f"d{i}", "action": "deploy"} for i in range(40)]
    plan = {
        "schema": "planlens-agent/plan/v1",
        "title": "Authored: 1,600 relationships",
        "steps": steps,
    }
    (project / "budget-catalog.json").write_text(json.dumps(catalog))
    (project / "budget-contract.json").write_text(json.dumps(contract))
    (project / "plans" / "budget.json").write_text(json.dumps(plan))


def main(cli: str, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    cli = str(Path(cli).resolve())  # commands below run from the project directory
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "release project"
        subprocess.run(
            [cli, "init", "release-basic", str(project)], check=True, capture_output=True
        )
        for name in ("compound.json", "malformed.json"):
            shutil.copy(ACCEPTANCE / "plans" / name, project / "plans" / name)
        for name in ("cyclic.toml", "unsupported-rule.toml"):
            shutil.copy(ACCEPTANCE / "contracts" / name, project / name)
        plan = json.loads((project / "plans" / "artifact-mismatch.json").read_text())
        plan["title"] = "Release </script><script>window.__pwned=1</script> & <b>bold</b> x"
        plan["steps"][1]["description"] = '<img src=x onerror="window.__pwned=2"> ‮RTL'
        (project / "plans" / "hostile.json").write_text(json.dumps(plan, indent=2))
        needle = "The artifact that is deployed must be the one that was tested and scanned."
        hostile = "<svg onload=window.__pwned=3> </script><script>window.__pwned=4</script>"
        contract = (project / "contract.toml").read_text().replace(needle, hostile)
        (project / "hostile-contract.toml").write_text(contract)

        write_large_case(project)
        write_budget_case(project)

        failed = 0
        for name, plan_path, contract_path, expected in CASES:
            base = [plan_path, "--contract", contract_path]
            base += ["--catalog", CATALOGS.get(name, "catalog.toml")]
            html = subprocess.run(
                [cli, "inspect", *base, "--output", "r.html"],
                cwd=project,
                capture_output=True,
                text=True,
            )
            sarif = subprocess.run(
                [cli, "check", *base, "--format", "sarif", "--output", "r.sarif"],
                cwd=project,
                capture_output=True,
                text=True,
            )
            ok = html.returncode == sarif.returncode == expected
            failed += not ok
            print(
                f"{'ok  ' if ok else 'FAIL'} {name:20} inspect exit {html.returncode}, "
                f"sarif exit {sarif.returncode}, expected {expected}"
            )
            shutil.copy(project / "r.html", out / f"{name}.html")
            shutil.copy(project / "r.sarif", out / f"{name}.sarif")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], Path(sys.argv[2])))
