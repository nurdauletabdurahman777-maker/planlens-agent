"""Owner-policy validation and untrusted-input handling."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from planlens_agent import check, validate_policy
from planlens_agent.loading import MAX_DEPTH
from planlens_agent.plan import MAX_STEPS
from planlens_agent.report import safe

from helpers import TEMPLATE

CONTRACT, CATALOG = TEMPLATE / "contract.toml", TEMPLATE / "catalog.toml"


def contract() -> dict[str, Any]:
    return tomllib.loads(CONTRACT.read_text())


def catalog() -> dict[str, Any]:
    return tomllib.loads(CATALOG.read_text())


def valid_plan() -> dict[str, Any]:
    return json.loads((TEMPLATE / "plans" / "valid.json").read_text())


def rules(policy_or_result: Any) -> list[str]:
    return sorted(d.rule_id for d in policy_or_result.diagnostics)


# --- owner policy --------------------------------------------------------------
def test_template_policy_is_valid_and_enforceable() -> None:
    p = validate_policy(CONTRACT, CATALOG)
    assert p.usable and p.diagnostics == ()


def test_toml_and_json_forms_of_a_policy_are_the_same_policy(tmp_path: Path) -> None:
    (tmp_path / "c.json").write_text(json.dumps(contract()))
    (tmp_path / "k.json").write_text(json.dumps(catalog()))
    a, b = (
        validate_policy(CONTRACT, CATALOG),
        validate_policy(tmp_path / "c.json", tmp_path / "k.json"),
    )
    assert a.contract and b.contract and a.contract.digest == b.contract.digest
    assert a.catalog and b.catalog and a.catalog.digest == b.catalog.digest


def test_three_action_cycle_is_reported_once_with_its_members() -> None:
    c = contract()
    c["rules"] += [
        {"id": "b", "kind": "prerequisite", "action": "unit_tests", "requires_prior": "deploy"},
        {"id": "c", "kind": "prerequisite", "action": "build", "requires_prior": "unit_tests"},
        {"id": "d", "kind": "prerequisite", "action": "deploy", "requires_prior": "build"},
    ]
    p = validate_policy(c, catalog())
    cycles = [d for d in p.diagnostics if d.rule_id == "contract/cycle"]
    assert len(cycles) == 1
    assert cycles[0].observed["actions"] == ["build", "deploy", "unit_tests"]
    assert not p.usable


def test_self_prerequisite_is_a_cycle() -> None:
    c = contract()
    c["rules"].append(
        {"id": "self", "kind": "prerequisite", "action": "deploy", "requires_prior": "deploy"}
    )
    assert "contract/cycle" in rules(validate_policy(c, catalog()))


def test_artifact_rule_over_incompatible_types_is_inconsistent() -> None:
    k = catalog()
    k["actions"]["publish_docs"] = {"inputs": {"artifact": "docs-bundle"}}
    k["actions"]["build_docs"] = {"outputs": {"artifact": "docs-bundle"}}
    c = contract()
    c["rules"].append(
        {
            "id": "mixed",
            "kind": "same_artifact",
            "inputs": ["deploy.artifact", "publish_docs.artifact"],
        }
    )
    assert rules(validate_policy(c, k)) == ["contract/inconsistent-artifact-rule"]


def test_required_action_whose_input_nothing_produces() -> None:
    k = catalog()
    k["actions"]["deploy"]["inputs"] = {"artifact": "signed-artifact"}
    p = validate_policy(contract(), k)
    assert "contract/unsatisfiable-input" in rules(p)


def test_artifact_rule_naming_an_input_the_action_lacks() -> None:
    c = contract()
    c["rules"][3]["inputs"] = ["unit_tests.artifact", "code_review.artifact"]
    assert rules(validate_policy(c, catalog())) == ["contract/unknown-action"]


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda c: c.update(extra_field=1), "contract/schema"),
        (lambda c: c.pop("catalog"), "contract/schema"),
        (lambda c: c.update(schema="planlens-agent/contract/v2"), "contract/unsupported-version"),
        (lambda c: c.update(schema="something-else"), "contract/schema"),
        (lambda c: c["rules"].append(dict(c["rules"][0])), "contract/schema"),  # duplicate rule id
        (lambda c: c["rules"][0].pop("id"), "contract/schema"),
        (lambda c: c["rules"][0].update(within_minutes=30), "contract/schema"),  # unknown field
        (lambda c: c["rules"][3].update(inputs=["deploy.artifact"]), "contract/schema"),
        (lambda c: c["required_actions"].append("build"), "contract/schema"),
    ],
)
def test_invalid_contracts_are_refused(mutate: Any, expected: str) -> None:
    c = contract()
    mutate(c)
    p = validate_policy(c, catalog())
    assert expected in rules(p) and not p.usable
    r = check(valid_plan(), c, catalog())
    assert r.verdict == "error" and not r.plan_evaluated


def test_unknown_rule_kind_is_unsupported_not_skipped() -> None:
    c = contract()
    c["rules"].append({"id": "x", "kind": "deploy_window", "days": ["mon"]})
    r = check(valid_plan(), c, catalog())
    assert r.verdict == "error" and r.error_kind == "unsupported" and r.plan_evaluated


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda k: k.update(schema="planlens-agent/catalog/v9"), "catalog/unsupported-version"),
        (lambda k: k["actions"].update({"Bad-Name": {}}), "catalog/schema"),
        (lambda k: k["actions"]["build"].update(effects=["writes"]), "catalog/schema"),
        (lambda k: k["actions"]["build"].update(outputs={"artifact": 3}), "catalog/schema"),
        (lambda k: k.update(actions={}), "catalog/schema"),
    ],
)
def test_invalid_catalogs_are_refused(mutate: Any, expected: str) -> None:
    k = catalog()
    mutate(k)
    r = check(valid_plan(), contract(), k)
    kind = "unsupported" if expected.endswith("unsupported-version") else "invalid_catalog"
    assert r.verdict == "error" and r.error_kind == kind and not r.plan_evaluated
    assert expected in rules(r)


def test_policy_files_that_do_not_exist(tmp_path: Path) -> None:
    r = check(valid_plan(), tmp_path / "nope.toml", CATALOG)
    assert r.verdict == "error" and rules(r) == ["contract/unreadable"]


def test_toml_syntax_error_has_a_real_source_span(tmp_path: Path) -> None:
    bad = tmp_path / "c.toml"
    bad.write_text('schema = "planlens-agent/contract/v1"\nid = \n')
    r = check(valid_plan(), bad, CATALOG)
    [d] = r.diagnostics
    assert d.rule_id == "contract/malformed"
    if d.location and d.location.source_span:  # TOMLDecodeError exposes it on 3.14+
        assert d.location.source_span.line == 2


# --- untrusted plan input ------------------------------------------------------
def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    f = tmp_path / "p.json"
    f.write_text('{"schema": "planlens-agent/plan/v1", "steps": [], "steps": []}')
    r = check(f, CONTRACT, CATALOG)
    assert rules(r) == ["plan/malformed"] and r.exit_code == 2


@pytest.mark.parametrize(
    "text",
    [
        '{"schema": "planlens-agent/plan/v1", "steps": NaN}',
        '{"schema": "planlens-agent/plan/v1", "steps": Infinity}',
    ],
)
def test_non_finite_numbers_are_rejected(tmp_path: Path, text: str) -> None:
    f = tmp_path / "p.json"
    f.write_text(text)
    assert rules(check(f, CONTRACT, CATALOG)) == ["plan/malformed"]


def test_excessive_nesting_is_rejected_without_crashing(tmp_path: Path) -> None:
    f = tmp_path / "p.json"
    f.write_text('{"schema": "planlens-agent/plan/v1", "steps": ' + "[" * 5000 + "]" * 5000 + "}")
    r = check(f, CONTRACT, CATALOG)
    assert r.exit_code == 2 and rules(r) == ["plan/limit"]
    deep: Any = []
    for _ in range(MAX_DEPTH + 5):
        deep = [deep]
    r2 = check({"schema": "planlens-agent/plan/v1", "steps": deep}, CONTRACT, CATALOG)
    assert r2.exit_code == 2 and rules(r2) == ["plan/limit"]


def test_step_limit_is_checked_before_evaluation() -> None:
    steps = [{"id": f"s{i}", "action": "notify"} for i in range(MAX_STEPS + 1)]
    r = check({"schema": "planlens-agent/plan/v1", "steps": steps}, CONTRACT, CATALOG)
    assert rules(r) == ["plan/limit"] and not r.plan_evaluated


def test_byte_limit_is_checked_before_parsing(tmp_path: Path) -> None:
    f = tmp_path / "big.json"
    f.write_text('{"schema": "planlens-agent/plan/v1", "description": "' + "x" * 1_100_000 + '"}')
    assert rules(check(f, CONTRACT, CATALOG)) == ["plan/limit"]


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda p: p.update(schema="planlens-agent/plan/v2"), "plan/unsupported-version"),
        (lambda p: p.pop("schema"), "plan/schema"),
        (lambda p: p.update(mode="parallel"), "plan/unsupported-semantics"),
        (lambda p: p.update(edges=[["build_rc", "test_rc"]]), "plan/unsupported-semantics"),
        (lambda p: p["steps"][1].update(depends_on=["build_rc"]), "plan/unsupported-semantics"),
        (lambda p: p["steps"][1].update(retry=3), "plan/unsupported-semantics"),
        (lambda p: p["steps"][1].update(colour="blue"), "plan/schema"),
        (
            lambda p: p["steps"][1]["inputs"].update(artifact="build_rc"),
            "plan/schema",
        ),  # bad ref syntax
        (lambda p: p["steps"][1].update(id="тест"), "plan/schema"),  # non-ASCII id
        (lambda p: p["steps"][1].pop("action"), "plan/schema"),
        (lambda p: p.update(steps=[]), "plan/schema"),
        (lambda p: p.update(steps={"a": 1}), "plan/schema"),
    ],
)
def test_plan_input_errors(mutate: Any, expected: str) -> None:
    p = valid_plan()
    mutate(p)
    r = check(p, CONTRACT, CATALOG)
    assert expected in rules(r) and r.exit_code == 2 and not r.plan_evaluated


def test_utf8_display_metadata_is_accepted_and_rendered() -> None:
    p = valid_plan()
    p["title"] = "Релиз 2.4.0 — выпуск"
    p["steps"][0]["description"] = "Сборка релиза / ビルド"
    assert check(p, CONTRACT, CATALOG).verdict == "pass"

    # Owner text reaches the output; check both renderings of it.
    bad = json.loads((TEMPLATE / "plans" / "reversed-review.json").read_text())
    c = contract()
    c["rules"][0]["because"] = "Релиз нельзя выкатывать до ревью."
    r = check(bad, c, catalog())
    assert "Релиз нельзя выкатывать до ревью." in r.render_text()  # readable in text
    js = r.to_json()
    assert js.isascii() and "\\u0420" in js  # escaped in JSON
    assert json.loads(js)["diagnostics"][0]["evidence"]["because"].startswith("Релиз")


def test_terminal_escapes_in_untrusted_text_are_neutralised() -> None:
    p = json.loads((TEMPLATE / "plans" / "reversed-review.json").read_text())
    c = contract()
    c["rules"][0]["because"] = "review first\x1b[2J\x1b[31m‮evil"
    text = check(p, c, catalog()).render_text()
    assert "\x1b" not in text and "‮" not in text
    assert "\\x1b[2J" in text and "\\u202e" in text
    assert safe("ok\x07") == "ok\\x07"


def test_a_mapping_passed_to_the_sdk_is_copied() -> None:
    p = valid_plan()
    r = check(p, CONTRACT, CATALOG)
    p["steps"].clear()
    assert r.plan_snapshot is not None and len(r.plan_snapshot["steps"]) == 6
    assert r.approve().integrity_ok()


def test_non_json_values_in_sdk_mappings_are_refused() -> None:
    import datetime

    p = valid_plan()
    p["title"] = datetime.date(2026, 1, 1)
    assert rules(check(p, CONTRACT, CATALOG)) == ["plan/schema"]


def test_wrong_argument_types_are_programming_errors() -> None:
    with pytest.raises(TypeError):
        check(valid_plan())  # no policy at all
    with pytest.raises(TypeError):
        check(42, CONTRACT, CATALOG)  # type: ignore[arg-type]


# --- A_REVIEW regression: numbers too large for the loader --------------------------
# Before the fix, an integer longer than CPython's 4300-digit conversion limit made
# check() raise ValueError, which the CLI reported as an internal error with no report.

HUGE = "9" * 5000


def test_huge_integer_in_a_json_plan_is_a_limit_finding(tmp_path: Path) -> None:
    f = tmp_path / "p.json"
    f.write_text('{"schema": "planlens-agent/plan/v1", "title": ' + HUGE + ', "steps": []}')
    r = check(f, CONTRACT, CATALOG)
    assert r.exit_code == 2 and rules(r) == ["plan/limit"]


def test_huge_integer_in_a_toml_contract_is_a_limit_finding(tmp_path: Path) -> None:
    f = tmp_path / "c.toml"
    f.write_text(
        CONTRACT.read_text().replace(
            'description = "A release', f'size = {HUGE}\ndescription = "A release', 1
        )
    )
    r = check(valid_plan(), f, CATALOG)
    assert r.exit_code == 2 and rules(r) == ["contract/limit"]


def test_huge_integer_in_an_sdk_mapping_is_a_limit_finding() -> None:
    p = valid_plan()
    p["title"] = 10**5000
    assert rules(check(p, CONTRACT, CATALOG)) == ["plan/limit"]


def test_huge_integer_through_the_cli_gives_a_report_not_an_internal_error(
    tmp_path: Path,
) -> None:
    from helpers import run_cli

    f = tmp_path / "p.json"
    f.write_text('{"schema": "planlens-agent/plan/v1", "title": ' + HUGE + ', "steps": []}')
    proc = run_cli(
        "check", str(f), "--contract", str(CONTRACT), "--catalog", str(CATALOG), "--format", "json"
    )
    assert proc.returncode == 2 and "internal error" not in proc.stderr
    assert [d["rule_id"] for d in json.loads(proc.stdout)["diagnostics"]] == ["plan/limit"]


def test_a_lowered_host_int_limit_still_gives_a_finding(tmp_path: Path) -> None:
    import sys

    f = tmp_path / "p.json"
    f.write_text('{"schema": "planlens-agent/plan/v1", "title": ' + "9" * 1000 + ', "steps": []}')
    old = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(640)
    try:
        assert rules(check(f, CONTRACT, CATALOG)) == ["plan/limit"]
    finally:
        sys.set_int_max_str_digits(old)


@pytest.mark.parametrize(
    "json_text, expected",
    [
        ("NaN", "plan/malformed"),  # not JSON
        ("-Infinity", "plan/malformed"),  # not JSON
        ("1e400", "plan/schema"),  # valid JSON, value overflows to infinity
        (HUGE, "plan/limit"),  # valid JSON, integer too long
    ],
)
def test_numeric_classification_follows_one_principle(
    tmp_path: Path, json_text: str, expected: str
) -> None:
    """malformed = not valid JSON text; schema = valid text, value outside the JSON
    model; limit = too large. The same value given as an SDK mapping, which has no
    text, is schema or limit - never malformed."""
    f = tmp_path / "p.json"
    f.write_text('{"schema": "planlens-agent/plan/v1", "title": ' + json_text + ', "steps": []}')
    assert rules(check(f, CONTRACT, CATALOG)) == [expected]


# --- Round 2, F3: unpaired surrogates ------------------------------------------------
# Before the fix a JSON file with "\ud800" parsed, then crashed canonical encoding:
# the SDK raised UnicodeEncodeError and the CLI printed an internal error, no report.

SURROGATE_DOCS = [
    ("plan", TEMPLATE / "plans" / "valid.json", "title"),
    ("contract", CONTRACT, "description"),
    ("catalog", CATALOG, "description"),
]


def _with_surrogate(tmp_path: Path, doc: str, src: Path, key: str) -> dict[str, Path]:
    data = json.loads(src.read_text()) if src.suffix == ".json" else tomllib.loads(src.read_text())
    data[key] = "release \ud800 candidate"
    target = tmp_path / f"{doc}.json"
    target.write_text(json.dumps(data))  # written as the escape \ud800
    assert "\\ud800" in target.read_text()
    paths = {"plan": TEMPLATE / "plans" / "valid.json", "contract": CONTRACT, "catalog": CATALOG}
    paths[doc] = target
    return paths


@pytest.mark.parametrize("doc, src, key", SURROGATE_DOCS, ids=[d[0] for d in SURROGATE_DOCS])
def test_unpaired_surrogate_in_a_file_is_a_schema_finding(
    tmp_path: Path, doc: str, src: Path, key: str
) -> None:
    paths = _with_surrogate(tmp_path, doc, src, key)
    r = check(paths["plan"], paths["contract"], paths["catalog"])
    [d] = r.diagnostics
    assert r.exit_code == 2 and d.rule_id == f"{doc}/schema"
    assert d.location is not None and d.location.path == f"/{key}"
    assert "\ud800" not in d.message  # the finding itself is valid text


@pytest.mark.parametrize("doc, src, key", SURROGATE_DOCS, ids=[d[0] for d in SURROGATE_DOCS])
def test_unpaired_surrogate_through_the_cli_gives_a_valid_report(
    tmp_path: Path, doc: str, src: Path, key: str
) -> None:
    from helpers import run_cli

    paths = _with_surrogate(tmp_path, doc, src, key)
    proc = run_cli(
        "check",
        str(paths["plan"]),
        "--contract",
        str(paths["contract"]),
        "--catalog",
        str(paths["catalog"]),
        "--format",
        "json",
    )
    assert proc.returncode == 2 and proc.stderr == ""
    report = json.loads(proc.stdout)
    assert [x["rule_id"] for x in report["diagnostics"]] == [f"{doc}/schema"]
    text = run_cli(
        "check",
        str(paths["plan"]),
        "--contract",
        str(paths["contract"]),
        "--catalog",
        str(paths["catalog"]),
    )
    assert text.returncode == 2 and text.stderr == "" and "Traceback" not in text.stdout


def test_unpaired_surrogate_in_sdk_mappings_matches_the_file_path() -> None:
    p = valid_plan()
    p["steps"][2]["description"] = "x\udfff"
    [d] = check(p, CONTRACT, CATALOG).diagnostics
    assert (d.rule_id, d.location.path if d.location else None) == (
        "plan/schema",
        "/steps/2/description",
    )
    k = catalog()
    k["actions"]["deploy"]["description"] = "\ud800"
    assert rules(check(valid_plan(), contract(), k)) == ["catalog/schema"]
    q = valid_plan()
    q["\ud800"] = 1  # in a key, not only a value
    assert rules(check(q, CONTRACT, CATALOG)) == ["plan/schema"]


def test_toml_refuses_a_surrogate_escape_as_malformed(tmp_path: Path) -> None:
    f = tmp_path / "c.toml"
    f.write_text(
        CONTRACT.read_text().replace(
            'description = "A release', 'description = "\\uD800 A release', 1
        )
    )
    [d] = check(valid_plan(), f, CATALOG).diagnostics
    assert d.rule_id == "contract/malformed" and d.location is not None
    # tomllib reports line/column as attributes only from Python 3.14; before that
    # the engine has no parser-supplied span and correctly reports none.
    import sys

    if sys.version_info >= (3, 14):
        assert d.location.source_span is not None
    else:
        assert d.location.source_span is None


def test_valid_non_bmp_text_is_accepted_and_identical_however_it_is_written(
    tmp_path: Path,
) -> None:
    escaped, raw = tmp_path / "escaped.json", tmp_path / "raw.json"
    body = json.loads((TEMPLATE / "plans" / "valid.json").read_text())
    body["title"] = "Release \U0001f680 2.4"
    raw.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    escaped.write_text(json.dumps(body, ensure_ascii=True))  # 🚀 pair
    assert "\\ud83d\\ude80" in escaped.read_text()
    a, b = check(escaped, CONTRACT, CATALOG), check(raw, CONTRACT, CATALOG)
    assert a.passed and b.passed
    assert a.plan_digest == b.plan_digest  # same value, same identity
    assert a.plan_snapshot is not None and a.plan_snapshot["title"] == "Release \U0001f680 2.4"


# --- Round 2, F4: required actions that can never run --------------------------------
def _boot(actions: dict[str, Any], required: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    cat = {"schema": "planlens-agent/catalog/v1", "id": "boot", "version": "1", "actions": actions}
    con = {
        "schema": "planlens-agent/contract/v1",
        "id": "boot",
        "version": "1",
        "catalog": {"id": "boot", "version": "1"},
        "required_actions": required,
    }
    return con, cat


PRODUCE = {"inputs": {"seed": "artifact"}, "outputs": {"result": "artifact"}}


def test_reviewer_reproduction_self_feeding_action_cannot_bootstrap() -> None:
    p = validate_policy(*_boot({"produce": PRODUCE}, ["produce"]))
    [d] = p.diagnostics
    assert not p.usable and d.rule_id == "contract/unsatisfiable-input" and d.rule.version == 2
    assert d.observed == {
        "unavailable_inputs": {"seed": "artifact"},
        "producers": {"artifact": ["produce"]},
    }


def test_independent_seed_producer_makes_it_satisfiable() -> None:
    """Positive control: the same type cycle with a reachable producer is fine."""
    con, cat = _boot(
        {"produce": PRODUCE, "fetch_seed": {"outputs": {"seed": "artifact"}}}, ["produce"]
    )
    p = validate_policy(con, cat)
    assert p.usable and p.diagnostics == ()
    witness = {
        "schema": "planlens-agent/plan/v1",
        "steps": [
            {"id": "s0", "action": "fetch_seed"},
            {"id": "s1", "action": "produce", "inputs": {"seed": "s0.seed"}},
        ],
    }
    assert check(witness, policy=p).passed


def test_two_action_type_cycle_without_and_with_an_entry_point() -> None:
    a = {"inputs": {"i": "x"}, "outputs": {"o": "y"}}
    b = {"inputs": {"i": "y"}, "outputs": {"o": "x"}}
    stuck = validate_policy(*_boot({"a": a, "b": b}, ["a"]))
    assert rules(stuck) == ["contract/unsatisfiable-input"]
    entry = validate_policy(*_boot({"a": a, "b": b, "c": {"outputs": {"o": "y"}}}, ["a"]))
    assert entry.usable and entry.diagnostics == ()  # c -> b -> a


def test_unrequired_unrunnable_actions_are_not_reported() -> None:
    con, cat = _boot({"produce": PRODUCE, "start": {}}, ["start"])
    assert validate_policy(con, cat).diagnostics == ()


def _passes_somehow(con: dict[str, Any], cat: dict[str, Any], max_len: int) -> bool:
    """Independent of the fixpoint: try every plan of up to max_len steps, binding each
    input to the most recent earlier step that outputs its type."""
    import itertools

    actions = cat["actions"]
    policy = validate_policy({**con, "required_actions": []}, cat)  # check plans, not policy
    for n in range(1, max_len + 1):
        for seq in itertools.product(sorted(actions), repeat=n):
            steps, ok = [], True
            for i, name in enumerate(seq):
                step: dict[str, Any] = {"id": f"s{i}", "action": name, "inputs": {}}
                for slot, t in actions[name].get("inputs", {}).items():
                    src = [j for j in range(i) if t in actions[seq[j]].get("outputs", {}).values()]
                    if not src:
                        ok = False
                        break
                    out = next(o for o, ty in actions[seq[src[-1]]]["outputs"].items() if ty == t)
                    step["inputs"][slot] = f"s{src[-1]}.{out}"
                if not ok:
                    break
                steps.append(step)
            if ok and set(con["required_actions"]) <= set(seq):
                if check(
                    {"schema": "planlens-agent/plan/v1", "steps": steps}, policy=policy
                ).passed:
                    return True
    return False


BOOT_SEEDS = range(40)


def _random_catalog(seed: int) -> dict[str, Any]:
    import random

    rng = random.Random(seed)
    types = ["x", "y", "z"]
    actions: dict[str, Any] = {}
    for name in ("a", "b", "c"):
        ins = rng.sample(types, rng.randint(0, 1))
        outs = rng.sample(types, rng.randint(1, 2))
        actions[name] = {
            "inputs": {f"in_{t}": t for t in ins},
            "outputs": {f"out_{t}": t for t in outs},
        }
    return actions


@pytest.mark.parametrize("seed", BOOT_SEEDS)
def test_bootstrap_agrees_with_exhaustive_search_on_small_catalogs(seed: int) -> None:
    actions = _random_catalog(seed)
    con, cat = _boot(actions, ["a"])
    usable = validate_policy(con, cat).usable
    # three actions: any runnable action is reachable within three steps
    assert usable == _passes_somehow(con, cat, max_len=3), actions


def test_the_random_catalogs_exercise_both_outcomes() -> None:
    usable = [validate_policy(*_boot(_random_catalog(s), ["a"])).usable for s in BOOT_SEEDS]
    assert sum(usable) >= 5 and usable.count(False) >= 5, (sum(usable), usable.count(False))


# --- filesystem errors while locating a document --------------------------------------------
# The B1 source-capture step called is_file() and resolve() outside the guarded read, so an
# over-long file name raised OSError on Python versions whose is_file() does not swallow it.
LONG_NAME = "x" * 300 + ".json"


def _arguments(doc: str, name: Any) -> tuple[Any, Any, Any]:
    args = {"plan": TEMPLATE / "plans" / "valid.json", "contract": CONTRACT, "catalog": CATALOG}
    args[doc] = name
    return args["plan"], args["contract"], args["catalog"]


@pytest.mark.parametrize("doc", ["plan", "contract", "catalog"])
def test_real_over_long_file_name_is_a_structured_finding(doc: str, tmp_path: Path) -> None:
    r = check(*_arguments(doc, tmp_path / LONG_NAME))
    assert r.exit_code == 2 and rules(r) == [f"{doc}/unreadable"]
    source = r.plan_source if doc == "plan" else getattr(r.policy, f"{doc}_source")
    assert source is None


@pytest.mark.parametrize("doc", ["plan", "contract", "catalog"])
def test_over_long_file_name_through_the_cli_gives_a_report(doc: str, tmp_path: Path) -> None:
    from helpers import run_cli

    plan, contract, catalog = _arguments(doc, LONG_NAME)
    proc = run_cli(
        "check",
        str(plan),
        "--contract",
        str(contract),
        "--catalog",
        str(catalog),
        "--format",
        "json",
        cwd=tmp_path,
    )
    assert proc.returncode == 2 and proc.stderr == ""
    assert [d["rule_id"] for d in json.loads(proc.stdout)["diagnostics"]] == [f"{doc}/unreadable"]


@pytest.mark.parametrize("doc", ["plan", "contract", "catalog"])
def test_injected_filesystem_error_is_a_structured_finding(
    doc: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Portable: behaves the same on every Python version and filesystem."""
    import errno

    name = tmp_path / f"broken-{doc}.json"
    real = Path.is_file

    def failing(self: Path) -> bool:
        if self == name:
            raise OSError(errno.ENAMETOOLONG, "File name too long")
        return real(self)

    monkeypatch.setattr(Path, "is_file", failing)
    r = check(*_arguments(doc, name))
    assert r.exit_code == 2 and rules(r) == [f"{doc}/unreadable"]
    assert "File name too long" in r.diagnostics[0].message


def test_failing_to_resolve_a_source_loses_the_location_not_the_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from planlens_agent import to_sarif

    expected = check(TEMPLATE / "plans" / "valid.json", CONTRACT, CATALOG)

    def failing(self: Path, strict: bool = False) -> Path:
        raise OSError(5, "I/O error")

    monkeypatch.setattr(Path, "resolve", failing)
    r = check(TEMPLATE / "plans" / "valid.json", CONTRACT, CATALOG)
    monkeypatch.undo()
    assert r.passed and r.plan_source is None
    assert r.policy.contract_source is None and r.policy.catalog_source is None
    assert r.plan_digest == expected.plan_digest
    assert r.to_dict()["inputs"]["plan"]["origin"] == expected.to_dict()["inputs"]["plan"]["origin"]
    assert "artifacts" not in to_sarif(r)["runs"][0]  # no guessed location


def test_successful_source_capture_is_unchanged() -> None:
    r = check(TEMPLATE / "plans" / "valid.json", CONTRACT, CATALOG)
    assert r.plan_source == str((TEMPLATE / "plans" / "valid.json").resolve())
    assert r.policy.contract_source == str(CONTRACT.resolve())


@pytest.mark.parametrize(
    "args",
    [
        ["regress", LONG_NAME],
        ["init", "release-basic", LONG_NAME],
        ["check", "x.json", "--format", "sarif", "--source-root", LONG_NAME],
    ],
    ids=["regress", "init", "source-root"],
)
def test_over_long_names_elsewhere_in_the_cli_are_usage_errors(
    args: list[str], tmp_path: Path
) -> None:
    from helpers import run_cli

    proc = run_cli(*args, cwd=tmp_path)
    assert proc.returncode == 2 and "internal error" not in proc.stderr
    assert "Traceback" not in proc.stderr
