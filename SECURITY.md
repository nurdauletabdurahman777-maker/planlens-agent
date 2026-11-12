# Security policy

## Supported versions

| Version | Security fixes |
| --- | --- |
| 0.1.x, the latest release | Yes |
| Development builds before 0.1.0 | No |

## Report a vulnerability

Do not open a public issue or pull request for a suspected vulnerability. Send a private message to the
maintainer, Nurdaulet Abdrakhman, on [LinkedIn](https://www.linkedin.com/in/nurdaulet-abdrakhman-523784375).

Include:

- the output of `planlens-agent --version`, your Python version and operating system;
- what an attacker controls, such as a plan or a saved run file, and what they gain;
- the smallest catalog, contract, plan and commands that reproduce it;
- whether you would like to be credited.

Leave out credentials and anything confidential. PlanLens is maintained by one person, so there is no fixed
response time; you will get a reply once the report has been read. A confirmed vulnerability is fixed in a new
release, and its release notes credit you if you wish.

## What counts

PlanLens treats candidate plans and saved run files as untrusted input. These are security problems:

- `ReferenceRunner` dispatches a plan that did not pass `check()`, or anything other than the approved snapshot;
- a crafted plan, catalog, contract or run file makes an Inspector report run script or load anything, despite
  its Content-Security-Policy;
- `check`, `inspect` or `suite` writes over a file it reads, despite the [`--output` protection](docs/suites.md#--output-and-inputs);
- input beyond a [documented limit](docs/semantics.md) is accepted, or exhausts memory or time instead of being
  refused;
- a plan that violates a supported, required rule passes.

## What does not

These are documented limits, not vulnerabilities:

- A pass describes the declared plan. It does not show that steps ran, that tests passed or what was deployed.
- Saved runs are not signed. A consistent edit, including its digests, is not detected; protect baselines as
  reviewed files.
- The catalog and contract are yours. Whoever can edit them can change your rules.
- Code that never calls PlanLens is not constrained by it.

Other bugs, including a wrong verdict that does not let a violating plan pass, go to
[GitHub issues](https://github.com/nurdauletabdurahman777-maker/planlens-agent/issues), as described in
[CONTRIBUTING](CONTRIBUTING.md#report-a-bug).
