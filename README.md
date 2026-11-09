<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/planlens-wordmark-dark.svg">
    <img alt="PlanLens" src="docs/brand/planlens-wordmark-light.svg" width="220">
  </picture>
</p>

<h1 align="center">Catch plan errors before execution.</h1>

<p align="center">Check structured AI-agent plans against your workflow rules. See which requirement failed and the steps involved.</p>

<p align="center">
  <picture>
    <source media="(prefers-reduced-motion: reduce) and (max-width: 600px)" srcset="docs/media/demo-narrow.png">
    <source media="(prefers-reduced-motion: reduce)" srcset="docs/media/demo.png">
    <source media="(max-width: 600px)" srcset="docs/media/demo-narrow.gif">
    <img alt="Walkthrough of an authored example. An authored plan has test_rc and scan_rc reference build_rc.artifact while deploy_prod references build_hotfix.artifact. PlanLens rejects it with plan/artifact-identity and the Inspector shows the references that disagree. A person edits a copy of the plan so deploy_prod references build_rc.artifact, and the recheck reports PASS. Nothing is built, tested or deployed." src="docs/media/demo.gif" width="100%">
  </picture>
</p>

<p align="center"><sub>Authored example. PlanLens compares the references a plan declares: nothing is built, tested or deployed, and a person makes the edit. On-screen notes are editorial. <a href="docs/media/demo.png">Static version</a> · <a href="docs/media/demo-transcript.txt">Complete transcript</a></sub></p>

<p align="center">
  <a href="#install-and-try-it">Install</a> ·
  <a href="docs/quickstart.md">Quickstart</a> ·
  <a href="docs/README.md">Documentation</a> ·
  <a href="examples/README.md">Examples</a>
</p>

<p align="center"><sub>First public release · Python 3.11+ · Apache-2.0 · No runtime dependencies</sub></p>

**For developers building plan-and-execute agents.** PlanLens reads one structured, sequential plan and checks it, like a linter,
against actions and workflow rules you own: required actions, step order, artifact references and same-artifact rules. It does not
run your steps. A pass means the declared plan meets your rules.

## Install and try it

You need Python 3.11 or newer; check with `python3 --version`. Installation downloads the package once. It has
no runtime dependencies, and after that it runs offline, with no model account and no telemetry. If something
fails, see [Troubleshooting](docs/quickstart.md#troubleshooting).

<!-- run contains="planlens-agent 0.1.0" -->
```sh
python3 -m venv planlens-env
. planlens-env/bin/activate
python -m pip install "planlens-agent==0.1.0"
planlens-agent --version
```

Reproduce the rejection on the bundled, authored example. Expect `RESULT: REJECTED (exit 1)` and
`plan/artifact-identity`:

<!-- run exit=1 contains="RESULT: REJECTED (exit 1) - 1 violation" contains="plan/artifact-identity" -->
```sh
planlens-agent init release-basic demo
cd demo
planlens-agent check plans/artifact-mismatch.json
```

Exit status 0 means pass, 1 rejected, and 2 invalid input or unsupported requirements. Generate the Inspector
report, and open `report.html` in a browser:

<!-- run exit=1 contains="wrote report.html: REJECTED (exit 1), 1 finding" -->
```sh
planlens-agent inspect plans/artifact-mismatch.json --output report.html
```

The [Quickstart](docs/quickstart.md) continues from here, through correcting a plan and writing your own workflow.
To work from source, see [CONTRIBUTING](CONTRIBUTING.md).

## The example in detail

The evidence behind the walkthrough. Build, tests, scan, review and deploy are all present, in a sensible order.

<details>
<summary><strong>The plan's steps</strong></summary>

```json
    {"id": "build_rc", "action": "build", "description": "Release candidate"},
    {"id": "build_hotfix", "action": "build", "description": "Rebuild with an untested patch"},
    {"id": "test_rc", "action": "unit_tests", "inputs": {"artifact": "build_rc.artifact"}},
    {"id": "scan_rc", "action": "security_scan", "inputs": {"artifact": "build_rc.artifact"}},
    {"id": "review", "action": "code_review"},
    {"id": "deploy_prod", "action": "deploy", "inputs": {"artifact": "build_hotfix.artifact"}}
```

<sub>Fragment of the authored plan `plans/artifact-mismatch.json`: the steps only.</sub>

<sub>Fragment of the authored plan `plans/artifact-mismatch.json`: the steps only.</sub>

</details>

<details>
<summary><strong>The diagnosis and a correction</strong></summary>

<br>

This is a saved result from the installed package, not a live check. [Reproduce it](#install-and-try-it).

**Rule:** `plan/artifact-identity`, from the contract rule `deploy-what-was-verified`.

| Step | `artifact` input refers to |
| --- | --- |
| `test_rc` | `build_rc.artifact` |
| `scan_rc` | `build_rc.artifact` |
| `deploy_prod` | `build_hotfix.artifact` |

Excerpt of the saved output:

```text
RESULT: REJECTED (exit 1) - 1 violation
...
    why:      The artifact that is deployed must be the one that was tested and scanned.
```

**One correction for this authored example,** on a copy of the plan. PlanLens reports that the references disagree; choosing which to change is yours. Here the plan describes `build_hotfix` as an untested patch, so the deployment changes:

```diff
-    {"id": "deploy_prod", "action": "deploy", "inputs": {"artifact": "build_hotfix.artifact"}}
+    {"id": "deploy_prod", "action": "deploy", "inputs": {"artifact": "build_rc.artifact"}}
```

**Recheck:**

```text
RESULT: PASS (exit 0) - 6 steps satisfy every supported required check
```

This checks the plan. Nothing was deployed. [Complete transcript](docs/media/demo-transcript.txt).

</details>

<details>
<summary><strong>The Inspector report</strong></summary>

<picture>
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="docs/media/inspector-finding-narrow.png">
  <source media="(max-width: 600px)" srcset="docs/media/inspector-finding-narrow-light.png">
  <source media="(prefers-color-scheme: dark)" srcset="docs/media/inspector-finding.png">
  <img alt="PlanLens inspector finding for an authored plan: violation of plan/artifact-identity. Observed references: test_rc and scan_rc refer to build_rc.artifact, while deploy_prod refers to build_hotfix.artifact. Required: one planned artifact across the tests, the scan and the deployment." src="docs/media/inspector-finding-light.png">
</picture>

**A real report for an authored plan.** `test_rc` and `scan_rc` reference `build_rc.artifact`, while `deploy_prod`
references `build_hotfix.artifact`, so the contract's same-artifact requirement fails. PlanLens compares the plan's
declared references. It did not inspect artifact bytes, run tests or deploy anything, and it cannot tell which
reference is the wrong one. [Full report image](docs/media/inspector.png).

</details>

## Integrate

PlanLens sits between your planner and whatever executes the plan.

```text
your planner
  proposes a plan (JSON)
    PlanLens checks it
      pass: the gate dispatches it
      reject: the gate dispatches nothing
```

You supply a catalog of allowed actions and a contract of workflow requirements, and the agent cannot change
either. The plan is treated as untrusted. Requirements PlanLens cannot evaluate end in exit 2 and are never
silently passed.

| Path | What it does | Guide |
| --- | --- | --- |
| CLI | `planlens-agent check`: text, JSON or SARIF, with exit statuses for scripts | [Quickstart](docs/quickstart.md) |
| Python SDK | `check()` returns the same verdict and diagnostics | [Integration](docs/integration.md) |
| Inspector | `planlens-agent inspect`: one self-contained HTML report | [Inspector](docs/inspector.md) |
| Comparison | `suite run` and `suite diff`: what changed after a planner, model or prompt change | [Comparing runs](docs/suites.md) |
| Execution gate | `approve()` and `ReferenceRunner` dispatch only the exact plan that passed | [Integration](docs/integration.md), [LangGraph recipe](examples/langgraph_gate/README.md) |
| CI | Workflows, exit statuses and reviewed baselines | [CI](docs/ci.md) |

## Scope and evidence

A pass means the **declared plan** satisfies your supported, required rules. It does not show that any step ran,
that tests passed, or what was deployed. Code that never calls PlanLens is not constrained by it. The public API
may evolve during 0.x.

| Area | Current state |
| --- | --- |
| Hosted checks | Run on Ubuntu, macOS and Windows, on CPython 3.11 to 3.14 |
| Examples in this README | Authored, not model output |
| Real planner output | One saved-response integration trial, independently reviewed. One response, not a reliability estimate |
| Onboarding | An implementer rehearsal only. External developer feedback has not been measured |
| SARIF | Export validated against the official schema. Hosted ingestion is unverified |

Details: [Evidence and limits](docs/evidence.md). Selected names and registration status: [Naming](docs/naming.md).

## Contributing and license

Maintained by Nurdaulet Abdrakhman ([LinkedIn](https://www.linkedin.com/in/nurdaulet-abdrakhman-523784375)). See [AUTHORS](AUTHORS.md) for authors and
[CONTRIBUTING](CONTRIBUTING.md) for setup, checks and bug reports.

Copyright 2026 Nurdaulet Abdrakhman. Licensed under the Apache License 2.0; see [LICENSE](LICENSE) and
[NOTICE](NOTICE). The OASIS SARIF schema in `tests/data/sarif/` is a separately
licensed test resource, outside Apache-2.0 and outside the wheel.
