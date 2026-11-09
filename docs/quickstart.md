# Quickstart

From nothing to a rule of your own. Every plan here is an **authored example**, not model output.
Run the blocks in order, from one empty folder, in one terminal.

## 1. What you need

**Once, to get the package** (this needs a network):

- Python 3.11 or newer. Check with `python3 --version`.
- Git, and read access to the private
  [repository](https://github.com/nurdauletabdurahman777-maker/planlens-agent).

**For normal use, nothing else.** After installation the tool works offline, with no account, API key
or telemetry. The only later network step is the optional LangGraph install in section 7.

The commands are for macOS and Linux shells. On Windows, activate with `planlens-env\Scripts\activate`;
these exact steps have not been followed there.

## 2. Install

Build the wheel. `pip` downloads the pinned build backend.

<!-- run prereq -->
```sh
git clone --branch launch/developer-experience https://github.com/nurdauletabdurahman777-maker/planlens-agent.git
python3 -m venv build-env
build-env/bin/python -m pip wheel --no-deps --wheel-dir wheelhouse ./planlens-agent
```

Install it. This step needs no network.

<!-- run contains="planlens-agent 0.1.0.dev0" -->
```sh
python3 -m venv planlens-env
. planlens-env/bin/activate
pip install --no-index wheelhouse/planlens_agent-0.1.0.dev0-py3-none-any.whl
planlens-agent --version
```

## 3. Run the first bundled check

`init` creates `release-basic/`: an action `catalog.toml`, a `contract.toml` and candidate `plans/`.
You own the catalog and contract. The agent only writes plans.

<!-- run contains="OK - contract release-basic 1.0.0 is valid" contains="RESULT: PASS (exit 0)" -->
```sh
planlens-agent init
cd release-basic
planlens-agent validate-policy
planlens-agent check plans/valid.json
```

Now a plan with every required action, and a deployment of the wrong build:

<!-- run exit=1 contains="RESULT: REJECTED (exit 1) - 1 violation" contains="plan/artifact-identity" -->
```sh
planlens-agent check plans/artifact-mismatch.json
```

Exit status 0 means pass, 1 rejected, and 2 invalid input or unsupported requirements.

## 4. Open the inspector

<!-- run exit=1 contains="wrote report.html: REJECTED (exit 1), 1 finding" -->
```sh
planlens-agent inspect plans/artifact-mismatch.json --output report.html
```

Open `report.html` in a browser: `open report.html` on macOS, `xdg-open report.html` on Linux. Start
with the verdict at the top right. The finding on the left has the message, what was observed and
required, and a **Next** row. The proposed sequence shows the steps and artifact references involved.
Offline help for any rule: `planlens-agent explain plan/artifact-identity`.

## 5. Correct a copy and recheck

Leave `plans/` as it is, because `fixtures.toml` records those files as expected outcomes. Tests and
the scan use `build_rc`, so point the deployment at it too:

<!-- run contains="RESULT: PASS (exit 0)" -->
```sh
sed 's/build_hotfix.artifact/build_rc.artifact/' plans/artifact-mismatch.json > my-plan.json
planlens-agent check my-plan.json
```

## 6. Describe your own workflow

Three actions and one rule: a database migration must follow a backup. Create the four files from a
new folder:

<!-- run -->
```sh
cd ..
mkdir my-workflow
cd my-workflow
cat > catalog.toml <<'EOF'
schema = "planlens-agent/catalog/v1"
id = "database-change"
version = "1.0.0"

[actions.backup]
description = "Back up the database."

[actions.migrate]
description = "Apply the schema migration."

[actions.notify]
description = "Tell the team."
EOF
cat > contract.toml <<'EOF'
schema = "planlens-agent/contract/v1"
id = "database-change"
version = "1.0.0"
catalog = { id = "database-change", version = "1.0.0" }
required_actions = ["backup", "migrate"]

[[rules]]
id = "backup-before-migrate"
kind = "prerequisite"
action = "migrate"
requires_prior = "backup"
because = "A migration needs a backup to roll back to."
EOF
cat > good.json <<'EOF'
{
  "schema": "planlens-agent/plan/v1",
  "steps": [
    {"id": "take_backup", "action": "backup"},
    {"id": "apply_migration", "action": "migrate"},
    {"id": "tell_team", "action": "notify"}
  ]
}
EOF
cat > out-of-order.json <<'EOF'
{
  "schema": "planlens-agent/plan/v1",
  "steps": [
    {"id": "apply_migration", "action": "migrate"},
    {"id": "take_backup", "action": "backup"},
    {"id": "tell_team", "action": "notify"}
  ]
}
EOF
```

Validate the policy and check the plan that follows the rule. `contract.toml` and `catalog.toml` in the
current folder are the defaults:

<!-- run contains="OK - contract database-change 1.0.0 is valid" contains="RESULT: PASS (exit 0)" -->
```sh
planlens-agent validate-policy
planlens-agent check good.json
```

Now the intentional violation:

<!-- run exit=1 contains="RESULT: REJECTED (exit 1) - 1 violation" contains="plan/prerequisite-order" contains="backup-before-migrate" -->
```sh
planlens-agent check out-of-order.json
```

The contract is the whole policy: required actions, and rules of kind `prerequisite` or `same_artifact`.
See [semantics](semantics.md).

## 7. Integrate

**From Python.** These run from the example folder. The first checks plans; the second shows that only an
approved plan reaches handlers.

<!-- run contains="a rejected plan cannot be approved, so nothing runs" -->
```sh
cd ../release-basic
python ../planlens-agent/examples/sdk_check.py
python ../planlens-agent/examples/guarded_runner.py
```

**In LangGraph.** This downloads the recipe's pinned, hashed dependencies, so it needs a network. LangGraph
is never a dependency of `planlens-agent` itself.

<!-- run online -->
```sh
pip install --require-hashes -r ../planlens-agent/examples/langgraph_gate/requirements.lock
```

A plan that passes runs its handlers; the handlers in this example only record that they were called:

<!-- run online contains="status:        executed" contains="handler calls: 6" -->
```sh
python ../planlens-agent/examples/langgraph_gate/gate.py plans/valid.json
```

A rejected plan reaches no handler:

<!-- run online exit=1 contains="status:        rejected" contains="handler calls: 0" -->
```sh
python ../planlens-agent/examples/langgraph_gate/gate.py plans/artifact-mismatch.json
```

## Where to go next

- [CI](ci.md): workflows, exit statuses and baselines
- [Comparing runs](suites.md): what changed after a planner change
- [Integration](integration.md): binding a check to execution, and its limits
- [LangGraph recipe](../examples/langgraph_gate/README.md): connect your own planner
- [Rules](rules.md) and [semantics](semantics.md): the reference
