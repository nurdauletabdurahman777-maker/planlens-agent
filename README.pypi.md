# PlanLens

**Catch plan errors before execution.**

Check structured AI-agent plans against your workflow rules. See which requirement failed and the steps involved.

![Walkthrough of an authored example. An authored plan has test_rc and scan_rc reference build_rc.artifact while deploy_prod references build_hotfix.artifact. PlanLens rejects it with plan/artifact-identity and the Inspector shows the references that disagree. A person edits a copy of the plan so deploy_prod references build_rc.artifact, and the recheck reports PASS. Nothing is built, tested or deployed.](https://raw.githubusercontent.com/nurdauletabdurahman777-maker/planlens-agent/v0.1.0/docs/media/demo.gif)

*Authored example. PlanLens compares the references a plan declares: nothing is built, tested or deployed, and a person makes the
edit. On-screen notes are editorial.*

For developers building plan-and-execute agents. PlanLens reads one structured, sequential plan and checks it, like a linter, against
actions and workflow rules you own: required actions, step order, artifact references and same-artifact rules. It does not run your
steps.

## Install

You need Python 3.11 or newer. PlanLens has no runtime dependencies, and after installation it runs offline, with
no model account and no telemetry.

<!-- run contains="planlens-agent 0.1.0" -->
```sh
python3 -m venv planlens-env
. planlens-env/bin/activate
python -m pip install "planlens-agent==0.1.0"
planlens-agent --version
```

## Try the bundled example

This authored example deliberately violates its contract. Expect `RESULT: REJECTED (exit 1)` and
`plan/artifact-identity`.

<!-- run exit=1 contains="RESULT: REJECTED (exit 1) - 1 violation" contains="plan/artifact-identity" -->
```sh
planlens-agent init release-basic demo
cd demo
planlens-agent check plans/artifact-mismatch.json
```

Exit status 0 means pass, 1 rejected, and 2 invalid input or unsupported requirements.

## Scope

A pass means the declared plan satisfies your supported, required rules. It does not show that any step ran, that
tests passed, or what was deployed. Code that never calls PlanLens is not constrained by it. The public API may
evolve during 0.x.

## Documentation

- [Quickstart](https://github.com/nurdauletabdurahman777-maker/planlens-agent/blob/v0.1.0/docs/quickstart.md)
- [Integration](https://github.com/nurdauletabdurahman777-maker/planlens-agent/blob/v0.1.0/docs/integration.md)
- [Inspector](https://github.com/nurdauletabdurahman777-maker/planlens-agent/blob/v0.1.0/docs/inspector.md)
- [Comparing runs](https://github.com/nurdauletabdurahman777-maker/planlens-agent/blob/v0.1.0/docs/suites.md)
- [CI](https://github.com/nurdauletabdurahman777-maker/planlens-agent/blob/v0.1.0/docs/ci.md)
- [Evidence and limits](https://github.com/nurdauletabdurahman777-maker/planlens-agent/blob/v0.1.0/docs/evidence.md)
- [Changelog](https://github.com/nurdauletabdurahman777-maker/planlens-agent/blob/v0.1.0/CHANGELOG.md)
- [Source](https://github.com/nurdauletabdurahman777-maker/planlens-agent)

## License

Copyright 2026 Nurdaulet Abdrakhman. Licensed under the Apache License 2.0; see [LICENSE](https://github.com/nurdauletabdurahman777-maker/planlens-agent/blob/v0.1.0/LICENSE) and
[NOTICE](https://github.com/nurdauletabdurahman777-maker/planlens-agent/blob/v0.1.0/NOTICE). Authors are listed in [AUTHORS](https://github.com/nurdauletabdurahman777-maker/planlens-agent/blob/v0.1.0/AUTHORS.md). Maintained by Nurdaulet Abdrakhman
([LinkedIn](https://www.linkedin.com/in/nurdaulet-abdrakhman-523784375)).
