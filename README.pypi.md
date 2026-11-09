# PlanLens

**Lint AI-agent plans before they run.**

PlanLens checks a sequential plan proposed by an AI agent against actions and workflow requirements you
own, and returns a deterministic verdict with specific diagnostics before any step runs. It is for developers
building plan-and-execute agents.

It catches steps in the wrong order, missing required actions, references to artifacts no earlier step
produces, and a deployment of a different build from the one that was tested.

![PlanLens inspector finding for an authored plan: violation of plan/artifact-identity. Observed references: test_rc and scan_rc refer to build_rc.artifact, while deploy_prod refers to build_hotfix.artifact. Required: one planned artifact across the tests, the scan and the deployment.](https://raw.githubusercontent.com/nurdauletabdurahman777-maker/planlens-agent/v0.1.0/docs/media/inspector-finding-light.png)

*A real report for an authored plan. PlanLens compared the plan's declared references. It did not inspect
artifact bytes, run tests or deploy anything.*

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
