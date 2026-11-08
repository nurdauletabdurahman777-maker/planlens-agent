"""The capture tool, with the real gate. Every plan here is an authored example."""

from __future__ import annotations

import hashlib
import json
import re
from importlib import resources
from pathlib import Path
from typing import Any

import capture_trial
import pytest
from capture_trial import main

from planlens_agent import check

TEMPLATE = Path(str(resources.files("planlens_agent").joinpath("templates", "release_basic")))
VALID = (TEMPLATE / "plans/valid.json").read_text()
MISMATCH = (TEMPLATE / "plans/artifact-mismatch.json").read_text()


@pytest.fixture
def base(tmp_path: Path) -> list[str]:
    (tmp_path / "request.txt").write_bytes(b"  ship 2.4.0\n\n")
    return [
        "--origin", "authored-example",
        "--request", str(tmp_path / "request.txt"),
        "--contract", str(TEMPLATE / "contract.toml"),
        "--catalog", str(TEMPLATE / "catalog.toml"),
    ]  # fmt: skip


def saved(tmp_path: Path, text: str | bytes) -> list[str]:
    path = tmp_path / "response.txt"
    path.write_bytes(text.encode() if isinstance(text, str) else text)
    return ["--raw-response", str(path)]


def load(out: Path, name: str) -> Any:
    return json.loads((out / name).read_text())


def test_a_passing_response_is_recorded_completely(base: list[str], tmp_path: Path) -> None:
    out = tmp_path / "trial"
    assert main([*base, *saved(tmp_path, VALID), "--out", str(out)]) == 0
    outcome = load(out, "outcome.json")
    assert outcome["status"] == "executed"
    assert outcome["origin"] == "authored-example"
    assert outcome["dry_run_handler_calls"] == [s["id"] for s in json.loads(VALID)["steps"]]
    assert (out / "request.txt").read_bytes() == b"  ship 2.4.0\n\n"
    assert (out / "raw_output.txt").read_text() == VALID
    assert (out / "policy/contract.toml").read_bytes() == (TEMPLATE / "contract.toml").read_bytes()
    assert load(out, "planner.json") == {"identity_status": "not supplied", "identity": None}
    report = load(out, "report.json")
    checked = (out / "checked_plan.json").read_bytes().rstrip(b"\n")
    assert hashlib.sha256(checked).hexdigest() == report["inputs"]["plan"]["sha256"]
    direct = check(json.loads(VALID), TEMPLATE / "contract.toml", TEMPLATE / "catalog.toml")
    assert report["inputs"]["plan"]["sha256"] == direct.plan_digest


def test_the_manifest_covers_every_file(base: list[str], tmp_path: Path) -> None:
    out = tmp_path / "trial"
    main([*base, *saved(tmp_path, VALID), "--out", str(out)])
    manifest = load(out, "manifest.json")
    on_disk = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    assert set(manifest) == on_disk - {"manifest.json"}
    for name, value in manifest.items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == value


def test_a_rejected_response_runs_nothing(base: list[str], tmp_path: Path) -> None:
    out = tmp_path / "trial"
    assert main([*base, *saved(tmp_path, MISMATCH), "--out", str(out)]) == 1
    outcome = load(out, "outcome.json")
    assert outcome["status"] == "rejected"
    assert outcome["findings"] == ["plan/artifact-identity"]
    assert outcome["dry_run_handler_calls"] == []


@pytest.mark.parametrize(
    "text",
    ["```json\n" + VALID + "```", "Here is the plan: " + VALID, "not json", '{"a": 1, "a": 2}'],
    ids=["fence", "prose", "text", "duplicate-key"],
)
def test_output_needing_repair_is_an_error_and_is_preserved(
    base: list[str], tmp_path: Path, text: str
) -> None:
    out = tmp_path / "trial"
    assert main([*base, *saved(tmp_path, text), "--out", str(out)]) == 2
    assert (out / "raw_output.txt").read_text() == text
    assert load(out, "outcome.json")["dry_run_handler_calls"] == []
    assert load(out, "conversion.json")["applied"] is False
    assert load(out, "conversion.json")["error"]
    assert not (out / "checked_plan.json").exists()


def test_undecodable_bytes_are_preserved(base: list[str], tmp_path: Path) -> None:
    out = tmp_path / "trial"
    assert main([*base, *saved(tmp_path, b"\xff\xfe"), "--out", str(out)]) == 2
    assert (out / "raw_output.txt").read_bytes() == b"\xff\xfe"


def test_a_conversion_is_explicit_and_recorded(base: list[str], tmp_path: Path) -> None:
    script = tmp_path / "strip_fence.py"
    script.write_text(
        "import json\n\n\ndef convert(raw):\n"
        "    return json.loads(raw.strip('`').removeprefix('json'))\n"
    )
    out = tmp_path / "trial"
    fenced = "```json\n" + VALID + "```"
    assert main([*base, *saved(tmp_path, fenced), "--convert", str(script), "--out", str(out)]) == 0
    record = load(out, "conversion.json")
    assert record["applied"] is True
    assert record["script_sha256"] == hashlib.sha256(script.read_bytes()).hexdigest()
    assert record["input_sha256"] == hashlib.sha256(fenced.encode()).hexdigest()
    assert (out / "convert.py").read_bytes() == script.read_bytes()
    assert (out / "raw_output.txt").read_text() == fenced  # still the original


def test_unsupported_fields_reach_the_checker(base: list[str], tmp_path: Path) -> None:
    plan = json.loads(VALID)
    plan["steps"][0]["parallel"] = True
    out = tmp_path / "trial"
    assert main([*base, *saved(tmp_path, json.dumps(plan)), "--out", str(out)]) != 0
    assert json.loads((out / "checked_plan.json").read_text())["steps"][0]["parallel"] is True
    assert load(out, "outcome.json")["dry_run_handler_calls"] == []


def planner_file(tmp_path: Path, body: str) -> str:
    path = tmp_path / "my_planner.py"
    path.write_text(f"def plan(request):\n    {body}\n")
    return f"{path}:plan"


def test_a_callable_receives_the_exact_request(base: list[str], tmp_path: Path) -> None:
    (tmp_path / "plan_text.json").write_text(VALID)
    target = str(tmp_path / "seen.txt")
    body = (
        f"open({target!r}, 'w').write(request); "
        f"return open({str(tmp_path / 'plan_text.json')!r}).read()"
    )
    out = tmp_path / "trial"
    assert main([*base, "--planner", planner_file(tmp_path, body), "--out", str(out)]) == 0
    assert Path(target).read_text() == "  ship 2.4.0\n\n"
    assert load(out, "outcome.json")["planner_source"] == "planner callable"
    assert (out / "raw_output.txt").read_text() == VALID


def test_a_mapping_says_its_original_text_is_not_available(base: list[str], tmp_path: Path) -> None:
    body = f"import json; return json.loads({VALID!r})"
    out = tmp_path / "trial"
    assert main([*base, "--planner", planner_file(tmp_path, body), "--out", str(out)]) == 0
    assert "not available" in load(out, "outcome.json")["raw_output"]["kind"]
    assert (out / "raw_output.json").exists()


def test_a_planner_failure_is_recorded_with_no_invented_output(
    base: list[str], tmp_path: Path
) -> None:
    body = "raise TimeoutError('no response')"
    out = tmp_path / "trial"
    assert main([*base, "--planner", planner_file(tmp_path, body), "--out", str(out)]) == 2
    outcome = load(out, "outcome.json")
    assert "no response" in outcome["reason"]
    assert outcome["raw_output"]["file"] is None
    assert not (out / "raw_output.txt").exists()


def test_identity_is_kept_as_supplied_and_marked_unverified(
    base: list[str], tmp_path: Path
) -> None:
    info = tmp_path / "info.json"
    info.write_text(json.dumps({"provider": None, "max_output_tokens": 500}))
    out = tmp_path / "trial"
    main([*base, *saved(tmp_path, VALID), "--planner-info", str(info), "--out", str(out)])
    record = load(out, "planner.json")
    assert record["identity"] == {"provider": None, "max_output_tokens": 500}
    assert "not verified" in record["identity_status"]


@pytest.mark.parametrize("field", ["api_key", "API-KEY", "auth_token", "client_secret"])
def test_credentials_in_planner_info_are_refused(
    base: list[str], tmp_path: Path, field: str, capsys: pytest.CaptureFixture[str]
) -> None:
    info = tmp_path / "info.json"
    info.write_text(json.dumps({"nested": {field: "x"}}))
    out = tmp_path / "trial"
    assert (
        main([*base, *saved(tmp_path, VALID), "--planner-info", str(info), "--out", str(out)]) == 2
    )
    assert not out.exists()
    assert "credential-like" in capsys.readouterr().err


def test_a_record_is_never_replaced(base: list[str], tmp_path: Path) -> None:
    out = tmp_path / "trial"
    out.mkdir()
    (out / "kept").write_text("earlier")
    assert main([*base, *saved(tmp_path, VALID), "--out", str(out)]) == 2
    assert (out / "kept").read_text() == "earlier"
    assert sorted(p.name for p in out.iterdir()) == ["kept"]


def test_an_unusable_policy_writes_nothing(base: list[str], tmp_path: Path) -> None:
    broken = tmp_path / "contract.toml"
    broken.write_text("not = [toml")
    args = list(base)
    args[args.index("--contract") + 1] = str(broken)
    out = tmp_path / "trial"
    assert main([*args, *saved(tmp_path, VALID), "--out", str(out)]) == 2
    assert not out.exists()


def test_the_origin_must_be_declared(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--request", "r", "--contract", "c", "--catalog", "k", "--raw-response", "x",
              "--out", str(tmp_path / "o")])  # fmt: skip


def test_the_gate_reads_the_preserved_policy_copies(
    base: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[Path, Path]] = []
    real = capture_trial.build_gate

    def spy(planner: Any, contract: Path, catalog: Path, handlers: Any, **options: Any) -> Any:
        seen.append((contract, catalog))
        return real(planner, contract, catalog, handlers, **options)

    monkeypatch.setattr(capture_trial, "build_gate", spy)
    out = tmp_path / "trial"
    assert main([*base, *saved(tmp_path, VALID), "--out", str(out)]) == 0
    assert seen == [(out / "policy/contract.toml", out / "policy/catalog.toml")]


# --- the frozen first-trial files ---------------------------------------------------
FIRST = Path(__file__).parent / "first-trial"
TASK = (
    "Create a sequential production-release plan for version 2.4.0 using the supplied action "
    "catalog and owner contract. Include the required build, unit-test, security-scan, code-review "
    "and deployment actions. Deployment must use the same planned artifact as testing and "
    "scanning. Satisfy every supplied requirement. Return only JSON conforming to "
    "planlens-agent/plan/v1."
)
SCHEMA = Path(str(resources.files("planlens_agent").joinpath("schemas", "plan.v1.schema.json")))


def frozen(base: list[str]) -> list[str]:
    args = list(base)
    args[args.index("--request") + 1] = str(FIRST / "request.txt")
    args[args.index("--contract") + 1] = str(FIRST / "contract.toml")
    args[args.index("--catalog") + 1] = str(FIRST / "catalog.toml")
    return [*args, "--planner-info", str(FIRST / "planner_info.json")]


def test_the_frozen_files_match_their_recorded_hashes() -> None:
    lines = (FIRST / "FROZEN.sha256").read_text().splitlines()
    assert sorted(line.split()[1] for line in lines) == [
        "catalog.toml",
        "contract.toml",
        "request.txt",
    ]
    for line in lines:
        value, name = line.split()
        assert hashlib.sha256((FIRST / name).read_bytes()).hexdigest() == value, name


@pytest.mark.parametrize("name", ["contract.toml", "catalog.toml"])
def test_the_frozen_policy_is_the_release_basic_template(name: str) -> None:
    assert (FIRST / name).read_bytes() == (TEMPLATE / name).read_bytes()


def test_the_request_has_the_task_and_the_actual_documents() -> None:
    request = (FIRST / "request.txt").read_text()
    assert request.startswith(TASK + "\n")
    for document in (SCHEMA, TEMPLATE / "catalog.toml", TEMPLATE / "contract.toml"):
        assert document.read_text().rstrip("\n") in request, document


def test_the_request_contains_no_example_plan() -> None:
    request = (FIRST / "request.txt").read_text()
    # The schema declares these fields as objects, so a quoted value can only come from a plan.
    assert not re.search(r'"(id|action)"\s*:\s*"', request)
    assert not re.search(r'"steps"\s*:\s*\[', request)


def test_unknown_identity_and_sampling_fields_are_null() -> None:
    info = json.loads((FIRST / "planner_info.json").read_text())
    assert info.pop("other_settings") == {}
    assert set(info.values()) == {None}


@pytest.mark.parametrize(
    ("response", "code", "status"),
    [
        (VALID, 0, "executed"),
        (MISMATCH, 1, "rejected"),
        ("I cannot produce that plan.", 2, "error"),
        ("```json\n" + VALID + "```", 2, "error"),
    ],
    ids=["pass", "reject", "prose", "fenced"],
)
def test_a_saved_response_is_preserved_whatever_it_is(
    base: list[str], tmp_path: Path, response: str, code: int, status: str
) -> None:
    out = tmp_path / "trial"
    label = "authored rehearsal of the first-trial files"
    args = [*frozen(base), *saved(tmp_path, response), "--label", label, "--out", str(out)]
    assert main(args) == code
    outcome = load(out, "outcome.json")
    assert outcome["status"] == status
    assert outcome["trial_label"] == label
    assert (out / "raw_output.txt").read_text() == response
    assert (out / "request.txt").read_bytes() == (FIRST / "request.txt").read_bytes()
    assert (out / "policy/contract.toml").read_bytes() == (FIRST / "contract.toml").read_bytes()
    assert load(out, "planner.json")["identity_status"] == "supplied by the operator, not verified"
