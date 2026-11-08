"""The inspector report: one recorded check, rendered safely and honestly."""

from __future__ import annotations

import copy
import dataclasses
import json
import re
import socket
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

import planlens_agent.inspector as inspector
from planlens_agent import build_view, check, render_html, to_sarif
from planlens_agent.inspector import COMPACT_ITEMS, DIAGRAM_MAX_ARCS, DIAGRAM_MAX_STEPS
from planlens_agent.view_model import group_label

from helpers import ACCEPTANCE, TEMPLATE, Page, load_cases, run_cli
from test_acceptance import _paths

CONTRACT, CATALOG = TEMPLATE / "contract.toml", TEMPLATE / "catalog.toml"
DEFAULTS, CASES = load_cases()


def page_for(plan: Any, contract: Any = CONTRACT, catalog: Any = CATALOG) -> Page:
    return Page(render_html(build_view(check(plan, contract, catalog))))


def plan(name: str) -> dict[str, Any]:
    return json.loads((TEMPLATE / "plans" / name).read_text())


# --- one result, three renderings ----------------------------------------------------------
def sarif_rule_ids(log: dict[str, Any]) -> list[str]:
    run = log["runs"][0]
    ids = [r["ruleId"] for r in run["results"]]
    for inv in run["invocations"]:
        ids += [n["descriptor"]["id"] for n in inv.get("toolConfigurationNotifications", [])]
    return sorted(ids)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_json_html_and_sarif_report_the_same_result(case: dict[str, Any]) -> None:
    result = check(*_paths(case))
    report = result.to_dict()
    expected = sorted(d["rule_id"] for d in report["diagnostics"])

    page = Page(render_html(build_view(result)))
    view = page.view()
    assert (view["verdict"], view["exit_code"]) == (report["verdict"], report["exit_code"])
    assert sorted(d["rule_id"] for d in view["diagnostics"]) == expected
    assert page.tags.count("button") == len(expected)  # one selectable entry each
    for d in report["diagnostics"]:
        assert d["message"] in page.visible  # readable without scripting
    label = {"pass": "PASS", "reject": "REJECTED", "error": "ERROR"}[report["verdict"]]
    assert f"{label} · exit {report['exit_code']}" in page.visible

    log = to_sarif(result)
    assert sarif_rule_ids(log) == expected
    assert log["runs"][0]["invocations"][0]["exitCode"] == report["exit_code"]
    assert log["runs"][0]["properties"]["verdict"] == report["verdict"]


def test_the_view_copies_verdicts_rather_than_deciding_them() -> None:
    """Change the recorded findings and the view follows: it has no opinion of its own."""
    result = check(plan("valid.json"), CONTRACT, CATALOG)
    forged = dataclasses.replace(result, verdict="reject")
    assert build_view(forged)["verdict"] == "reject"


def test_focus_comes_from_structure_not_wording() -> None:
    result = check(plan("artifact-mismatch.json"), CONTRACT, CATALOG)
    blanked = dataclasses.replace(
        result,
        diagnostics=tuple(
            dataclasses.replace(d, message="", suggestion="") for d in result.diagnostics
        ),
    )
    focus = [d["focus"] for d in build_view(result)["diagnostics"]]
    assert focus == [d["focus"] for d in build_view(blanked)["diagnostics"]]
    assert focus[0]["steps"] == [2, 3, 5] and focus[0]["producer_steps"] == [0, 1]


def test_script_has_no_verification_or_network_code() -> None:
    js = resources.files("planlens_agent").joinpath("inspector", "inspector.js").read_text()
    for word in (
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "eval(",
        "import(",
        "http:",
        "https:",
        "localStorage",
        "sendBeacon",
    ):
        assert word not in js
    for fact in ("required_actions", "prerequisites", "catalog", r"same_artifact\b"):
        assert not re.search(fact, js)  # no policy data is consulted


# --- what each case shows -------------------------------------------------------------------
def test_valid_plan_shows_sequence_and_no_findings() -> None:
    page = page_for(plan("valid.json"))
    assert ("class", "diagramwrap") in page.attrs_seen  # control for the checks below
    assert "PASS" in page.visible and "No findings." in page.visible
    view = page.view()
    assert [s["id"] for s in view["plan"]["steps"]] == [
        "build_rc",
        "test_rc",
        "scan_rc",
        "review",
        "deploy_prod",
        "announce",
    ]
    assert [p["rule_id"] for p in view["prerequisites"]] == [
        "review-before-deploy",
        "tests-before-deploy",
        "scan-before-deploy",
    ]
    assert view["identity"]["contract"]["id"] == "release-basic"


def test_legend_explains_only_what_the_diagram_contains() -> None:
    valid = page_for(plan("valid.json")).visible
    assert "prerequisite step listed earlier" in valid
    for absent in ("listed later", "step with findings", "selected finding", "not in catalog"):
        assert absent not in valid
    assert "listed later" in page_for(plan("reversed-review.json")).visible
    assert "selected finding compares" in page_for(plan("artifact-mismatch.json")).visible


def test_reversed_prerequisite_points_at_the_rule_and_both_steps() -> None:
    view = page_for(plan("reversed-review.json")).view()
    [d] = view["diagnostics"]
    assert d["focus"]["steps"] == [3, 4]
    assert d["focus"]["prerequisite_rule"] == "review-before-deploy"
    rule = next(p for p in view["prerequisites"] if p["rule_id"] == "review-before-deploy")
    assert rule["prior_steps"] == [4] and rule["action_steps"] == [3]  # listed later


def test_same_artifact_shows_every_binding_without_naming_a_culprit() -> None:
    page = page_for(plan("artifact-mismatch.json"))
    [rule] = page.view()["same_artifact"]
    assert [(b["step_id"], b["ref"], b["group"]) for b in rule["bindings"]] == [
        ("test_rc", "build_rc.artifact", "A"),
        ("scan_rc", "build_rc.artifact", "A"),
        ("deploy_prod", "build_hotfix.artifact", "B"),
    ]
    assert [(g["label"], g["bindings"]) for g in rule["groups"]] == [("A", 2), ("B", 1)]
    assert "cannot tell which group is the wrong one" in page.visible
    for claim in ("wrong artifact", "should be group", "correct group", "is incorrect"):
        assert claim not in page.visible.lower()


def test_a_passing_same_artifact_rule_reports_no_finding_and_one_group() -> None:
    page = page_for(plan("valid.json"))
    [rule] = page.view()["same_artifact"]
    assert [g["label"] for g in rule["groups"]] == ["A"] and rule["finding_ids"] == []
    assert "No finding was reported for this rule." in page.visible


def test_missing_action_is_shown_absent_with_no_invented_step() -> None:
    page = page_for(ACCEPTANCE / "plans" / "compound.json")
    view = page.view()
    missing = next(d for d in view["diagnostics"] if d["rule_id"] == "plan/missing-required-action")
    assert missing["location"] is None and missing["focus"]["steps"] == []
    assert missing["focus"]["required_action"] == "code_review"
    assert "code_review" not in [s["action"] for s in view["plan"]["steps"]]
    assert "no step performs this action" in page.visible
    assert "No plan location: the required step does not exist in the plan" in page.visible
    [row] = [r for r in view["required_actions"] if r["action"] == "code_review"]
    assert not row["present"] and row["step_indices"] == [] and row["finding_ids"]


def test_unknown_action_and_unresolved_reference_are_labelled() -> None:
    page = page_for(ACCEPTANCE / "plans" / "compound.json")
    assert "(not in catalog)" in page.visible
    page = page_for(ACCEPTANCE / "plans" / "unresolved-reference.json")
    unresolved = {u["ref"] for u in page.view()["artifacts"]["unresolved"]}
    assert unresolved == {"build_candidate.artifact", "build_rc.binary"}
    assert "no matching producer output in the plan" in page.visible


@pytest.mark.parametrize(
    "plan_path, contract",
    [
        (ACCEPTANCE / "plans" / "malformed.json", CONTRACT),
        (ACCEPTANCE / "plans" / "duplicate-step-id.json", CONTRACT),
        (ACCEPTANCE / "plans" / "unsupported-semantics.json", CONTRACT),
        (TEMPLATE / "plans" / "valid.json", ACCEPTANCE / "contracts" / "cyclic.toml"),
        (TEMPLATE / "plans" / "valid.json", ACCEPTANCE / "contracts" / "unknown-action.toml"),
        (TEMPLATE / "plans" / "valid.json", Path("does-not-exist.toml")),
    ],
    ids=[
        "malformed",
        "duplicate-id",
        "unsupported-semantics",
        "cycle",
        "unknown-action",
        "missing",
    ],
)
def test_unevaluated_checks_render_no_invented_graph(plan_path: Path, contract: Path) -> None:
    page = page_for(plan_path, contract)
    view = page.view()
    assert view["verdict"] == "error" and view["plan"] is None
    assert ("class", "diagramwrap") not in page.attrs_seen
    assert "Plan not evaluated" in page.visible
    assert not [a for a in page.attrs_seen if a[0] == "data-step"]
    assert "Proposed sequence" not in page.visible
    assert view["diagnostics"]


def test_unsupported_rule_shows_the_evaluated_plan_and_says_it_cannot_pass() -> None:
    page = page_for(
        TEMPLATE / "plans" / "valid.json", ACCEPTANCE / "contracts" / "unsupported-rule.toml"
    )
    view = page.view()
    assert view["verdict"] == "error" and view["plan"] is not None
    assert "ERROR" in page.visible and "cannot be evaluated by this engine" in page.visible


def test_invalid_policy_names_the_document_it_could_not_use(tmp_path: Path) -> None:
    bad = tmp_path / "broken.toml"
    bad.write_text("schema = \n")
    page = page_for(TEMPLATE / "plans" / "valid.json", bad)
    assert "could not be used" in page.visible and "broken.toml" in page.visible


def test_mapping_backed_check_renders_with_logical_locations_only() -> None:
    p = plan("reversed-review.json")
    page = page_for(p, tomllib.loads(CONTRACT.read_text()), tomllib.loads(CATALOG.read_text()))
    view = page.view()
    assert view["identity"]["plan"]["origin"] == "<plan mapping>"
    assert "&lt;plan mapping&gt;" not in page.visible and "<plan mapping>" in page.visible
    assert view["diagnostics"][0]["location"]["source_span"] is None
    assert not re.search(r"\bline \d+", page.visible)


def test_real_source_span_is_shown_and_plan_positions_are_not_called_lines() -> None:
    page = page_for(ACCEPTANCE / "plans" / "malformed.json")
    assert "line 5, column 46" in page.visible
    page = page_for(plan("reversed-review.json"))
    assert not re.search(r"\bline \d+", page.visible)


def test_long_identifiers_and_descriptions_stay_complete_in_the_data_and_text() -> None:
    p = plan("valid.json")
    long_id = "b" + "x" * 63
    text = json.dumps(p).replace("build_rc", long_id)
    p = json.loads(text)
    p["steps"][0]["description"] = "word " * 300
    page = page_for(p)
    assert long_id in page.visible and page.view()["verdict"] == "pass"


def test_very_long_plan_omits_the_diagram_but_keeps_every_step() -> None:
    steps = [{"id": "b", "action": "build"}]
    steps += [
        {"id": f"t{i}", "action": "unit_tests", "inputs": {"artifact": "b.artifact"}}
        for i in range(DIAGRAM_MAX_STEPS + 5)
    ]
    steps += [
        {"id": "s", "action": "security_scan", "inputs": {"artifact": "b.artifact"}},
        {"id": "r", "action": "code_review"},
        {"id": "d", "action": "deploy", "inputs": {"artifact": "b.artifact"}},
    ]
    page = page_for({"schema": "planlens-agent/plan/v1", "steps": steps})
    assert ("class", "diagramwrap") not in page.attrs_seen
    assert "The diagram is omitted" in page.visible
    assert sum(1 for a in page.attrs_seen if a[0] == "data-step") >= len(steps)


# --- hostile input --------------------------------------------------------------------------
HOSTILE = [
    "</script><script>window.pwned=1</script>",
    "<img src=x onerror=window.pwned=2>",
    '"><svg onload=window.pwned=3>',
    "<!-- --><style>body{display:none}</style>",
    "]]>&amp;&#x3c;script&#x3e;",
    "   </SCRIPT > <ScRiPt>",
    "\x1b[31m‮ reversed",
    '<a href="javascript:window.pwned=4">x</a>',
]
ALLOWED_TAGS = {
    "html",
    "head",
    "meta",
    "title",
    "style",
    "body",
    "a",
    "div",
    "header",
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "svg",
    "circle",
    "path",
    "rect",
    "text",
    "g",
    "dl",
    "dt",
    "dd",
    "section",
    "span",
    "br",
    "ol",
    "ul",
    "li",
    "button",
    "pre",
    "code",
    "table",
    "caption",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "footer",
    "noscript",
    "script",
    "strong",
    "details",
    "summary",
}


def hostile_inputs(text: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    p = plan("artifact-mismatch.json")
    p["title"] = p["description"] = text
    for s in p["steps"]:
        s["description"] = text
    con = tomllib.loads(CONTRACT.read_text())
    con["description"] = text
    for r in con["rules"]:
        r["because"] = text
    return p, con, tomllib.loads(CATALOG.read_text())


def assert_inert(document: str, text: str) -> None:
    page = Page(document)
    assert set(page.tags) <= ALLOWED_TAGS, set(page.tags) - ALLOWED_TAGS
    assert page.tags.count("script") == 2  # only the two of ours
    assert not [n for n, _ in page.attrs_seen if n.startswith("on")]
    assert not [n for n, _ in page.attrs_seen if n in ("src", "srcset", "style")]
    assert not [v for n, v in page.attrs_seen if n == "href" and not (v or "").startswith("#")]
    view = page.view()
    assert view["plan"]["title"] == text  # data survives exactly


@pytest.mark.parametrize("text", HOSTILE)
def test_hostile_strings_are_inert_text(text: str) -> None:
    assert_inert(render_html(build_view(check(*hostile_inputs(text)))), text)


def test_hostile_strings_are_visible_literally() -> None:
    text = HOSTILE[0]
    page = Page(render_html(build_view(check(*hostile_inputs(text)))))
    assert text in page.visible


def test_control_and_bidi_characters_are_escaped_in_rendered_text() -> None:
    text = HOSTILE[6]
    document = render_html(build_view(check(*hostile_inputs(text))))
    assert "\x1b" not in document and "‮" not in document
    assert "\\x1b[31m" in Page(document).visible


def test_negative_control_html_escaping(monkeypatch: pytest.MonkeyPatch) -> None:
    """If escaping is removed, the checks above must fail."""
    monkeypatch.setattr(inspector, "e", lambda value: str(value))
    with pytest.raises(AssertionError):
        assert_inert(render_html(build_view(check(*hostile_inputs(HOSTILE[1])))), HOSTILE[1])


def test_negative_control_embedded_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inspector, "_embed_json", lambda data: json.dumps(data))
    with pytest.raises((AssertionError, ValueError)):
        assert_inert(render_html(build_view(check(*hostile_inputs(HOSTILE[0])))), HOSTILE[0])


def test_page_is_self_contained_and_loads_nothing() -> None:
    document = render_html(build_view(check(plan("artifact-mismatch.json"), CONTRACT, CATALOG)))
    page = Page(document)
    assert "link" not in page.tags and "img" not in page.tags and "iframe" not in page.tags
    assert not re.search(r"https?://", document.replace("http://www.w3.org", ""))
    assert "url(" not in document and "@import" not in document and "@font-face" not in document
    [csp] = re.findall(r'Content-Security-Policy" content="([^"]*)"', document)
    assert csp.startswith("default-src 'none'")
    assert "unsafe" not in csp and "'sha256-" in csp and "http" not in csp


def test_rendering_is_deterministic() -> None:
    args = (plan("artifact-mismatch.json"), CONTRACT, CATALOG)
    assert render_html(build_view(check(*args))) == render_html(build_view(check(*args)))


def test_rendering_works_with_networking_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*a: object, **k: object) -> None:
        raise AssertionError("attempted to open a network socket")

    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    for case in CASES:
        result = check(*_paths(case))
        assert render_html(build_view(result)) and to_sarif(result)


def test_rendering_does_not_touch_the_snapshot() -> None:
    result = check(plan("valid.json"), CONTRACT, CATALOG)
    before = copy.deepcopy(result.to_dict())
    render_html(build_view(result))
    assert result.to_dict() == before and result.approve().integrity_ok()


# --- CLI ------------------------------------------------------------------------------------
@pytest.fixture
def project(tmp_path: Path) -> Path:
    target = tmp_path / "my release project"
    assert run_cli("init", "release-basic", str(target)).returncode == 0
    return target


@pytest.mark.parametrize(
    "name, code, word",
    [
        ("valid.json", 0, "PASS"),
        ("reversed-review.json", 1, "REJECTED"),
        ("artifact-mismatch.json", 1, "REJECTED"),
    ],
)
def test_inspect_command(project: Path, name: str, code: int, word: str) -> None:
    proc = run_cli("inspect", f"plans/{name}", "--output", "my report.html", cwd=project)
    assert proc.returncode == code and proc.stderr == ""
    assert f"my report.html: {word} (exit {code})" in proc.stdout
    assert (
        Page((project / "my report.html").read_text(encoding="utf-8")).view()["exit_code"] == code
    )


def test_inspect_writes_a_report_for_invalid_input(project: Path) -> None:
    proc = run_cli("inspect", "plans/nope.json", "--output", "r.html", cwd=project)
    assert proc.returncode == 2 and "Traceback" not in proc.stderr
    assert "plan/unreadable" in (project / "r.html").read_text(encoding="utf-8")


def test_inspect_uses_the_policy_arguments(project: Path) -> None:
    other = project / "looser.toml"
    other.write_text((project / "contract.toml").read_text().split("[[rules]]")[0])
    proc = run_cli(
        "inspect",
        "plans/reversed-review.json",
        "--contract",
        "looser.toml",
        "--output",
        "r.html",
        cwd=project,
    )
    assert proc.returncode == 0


def test_inspect_usage_and_write_errors_are_exit_2(project: Path) -> None:
    assert run_cli("inspect", "plans/valid.json", cwd=project).returncode == 2
    proc = run_cli("inspect", "plans/valid.json", "--output", "no/such/dir/r.html", cwd=project)
    assert proc.returncode == 2 and "cannot write" in proc.stderr and "Traceback" not in proc.stderr


# --- colour contrast ------------------------------------------------------------------------
def _tokens() -> tuple[dict[str, str], dict[str, str]]:
    css = resources.files("planlens_agent").joinpath("inspector", "inspector.css").read_text()
    light_block = css[css.index(":root {") : css.index("@media (prefers-color-scheme: dark)")]
    dark_block = css[css.index("@media (prefers-color-scheme: dark)") : css.index("* { box-sizing")]
    pattern = r"--([a-z-]+):\s*(#[0-9a-f]{6});"
    return dict(re.findall(pattern, light_block)), dict(re.findall(pattern, dark_block))


def _luminance(colour: str) -> float:
    channels = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


TEXT_PAIRS = [
    ("ink", "panel"),
    ("ink", "bg"),
    ("ink", "accent-soft"),
    ("muted", "panel"),
    ("muted", "bg"),
    ("muted", "accent-soft"),
    ("accent", "panel"),
    ("accent", "accent-soft"),
    ("reject", "panel"),
    ("reject", "reject-soft"),
    ("pass", "pass-soft"),
    ("error", "panel"),
    ("error", "error-soft"),
    ("grp-ink", "grp-a"),
    ("grp-ink", "grp-b"),
    ("grp-ink", "grp-c"),
    ("grp-ink", "grp-d"),
]


@pytest.mark.parametrize("scheme", [0, 1], ids=["light", "dark"])
@pytest.mark.parametrize("fg, bg", TEXT_PAIRS, ids=[f"{a}-on-{b}" for a, b in TEXT_PAIRS])
def test_text_colours_meet_wcag_aa(fg: str, bg: str, scheme: int) -> None:
    tokens = _tokens()[scheme]
    assert _contrast(tokens[fg], tokens[bg]) >= 4.5, (tokens[fg], tokens[bg])


# --- artifact-group labels past Z and ZZ ---------------------------------------------------
@pytest.mark.parametrize(
    "n, label",
    [
        (0, "A"),
        (1, "B"),
        (25, "Z"),
        (26, "AA"),
        (27, "AB"),
        (51, "AZ"),
        (52, "BA"),
        (701, "ZZ"),
        (702, "AAA"),
        (703, "AAB"),
        (18277, "ZZZ"),
        (18278, "AAAA"),
    ],
)
def test_group_label_boundaries(n: int, label: str) -> None:
    assert group_label(n) == label


def test_group_labels_are_distinct_and_never_shrink() -> None:
    labels = [group_label(n) for n in range(30000)]
    assert len(set(labels)) == len(labels)
    assert all(len(a) <= len(b) for a, b in zip(labels, labels[1:], strict=False))


def many_references(references: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """The review's reproduction, for any number of distinct planned artifacts."""
    outputs = {f"o{i}": "artifact" for i in range(32)}
    catalog = {
        "schema": "planlens-agent/catalog/v1",
        "id": "g",
        "version": "1",
        "actions": {
            "build": {"outputs": outputs},
            "use": {"inputs": {"artifact": "artifact"}},
            "deploy": {"inputs": {"artifact": "artifact"}},
        },
    }
    contract = {
        "schema": "planlens-agent/contract/v1",
        "id": "g",
        "version": "1",
        "catalog": {"id": "g", "version": "1"},
        "required_actions": ["build", "use", "deploy"],
        "rules": [
            {"id": "same", "kind": "same_artifact", "inputs": ["use.artifact", "deploy.artifact"]}
        ],
    }
    builds = -(-references // 32)
    steps: list[dict[str, Any]] = [{"id": f"b{i}", "action": "build"} for i in range(builds)]
    steps += [
        {"id": f"u{i}", "action": "use", "inputs": {"artifact": f"b{i // 32}.o{i % 32}"}}
        for i in range(references)
    ]
    steps.append({"id": "d", "action": "deploy", "inputs": {"artifact": "b0.o0"}})
    return {"schema": "planlens-agent/plan/v1", "steps": steps}, contract, catalog


@pytest.mark.parametrize(
    "references, last", [(26, "Z"), (27, "AA"), (702, "ZZ"), (703, "AAA"), (968, "AKF")]
)
def test_group_labels_through_the_sdk(references: int, last: str) -> None:
    result = check(*many_references(references))
    assert result.verdict == "reject"
    assert {d.rule_id for d in result.diagnostics} == {"plan/artifact-identity"}
    page = Page(render_html(build_view(result)))
    view = page.view()
    [rule] = view["same_artifact"]
    assert rule["groups"][0]["label"] == "A" and rule["groups"][-1]["label"] == last
    assert rule["groups"][0]["bindings"] == 2 and len(rule["groups"]) == references
    assert view["verdict"] == "reject" and "REJECTED" in page.visible


def test_reviewer_reproduction_through_the_cli(tmp_path: Path) -> None:
    plan_doc, contract, catalog = many_references(703)
    assert len(plan_doc["steps"]) == 726
    for name, doc in (("plan", plan_doc), ("contract", contract), ("catalog", catalog)):
        (tmp_path / f"{name}.json").write_text(json.dumps(doc))
    args = ["plan.json", "--contract", "contract.json", "--catalog", "catalog.json"]
    proc = run_cli("inspect", *args, "--output", "r.html", cwd=tmp_path)
    assert proc.returncode == 1 and proc.stderr == "", proc.stderr
    assert "REJECTED (exit 1), 1 finding" in proc.stdout
    view = Page((tmp_path / "r.html").read_text(encoding="utf-8")).view()
    assert view["same_artifact"][0]["groups"][-1]["label"] == "AAA"
    for fmt in ("json", "sarif"):
        other = run_cli("check", *args, "--format", fmt, cwd=tmp_path)
        assert other.returncode == 1 and json.loads(other.stdout)


def test_a_label_means_one_reference_across_rules() -> None:
    catalog = tomllib.loads(CATALOG.read_text())
    contract = tomllib.loads(CONTRACT.read_text())
    contract["rules"] = [
        {
            "id": "first",
            "kind": "same_artifact",
            "inputs": ["unit_tests.artifact", "deploy.artifact"],
        },
        {
            "id": "second",
            "kind": "same_artifact",
            "inputs": ["security_scan.artifact", "deploy.artifact"],
        },
    ]
    p = plan("valid.json")
    p["steps"].insert(1, {"id": "other", "action": "build"})
    p["steps"][3]["inputs"]["artifact"] = "other.artifact"  # the scan uses another build
    view = page_for(p, contract, catalog).view()
    meaning: dict[str, set[str]] = {}
    for rule in view["same_artifact"]:
        for g in rule["groups"]:
            meaning.setdefault(g["label"], set()).add(g["ref"])
    assert meaning == {"A": {"build_rc.artifact"}, "B": {"other.artifact"}}


def test_two_letter_labels_fit_their_diagram_badge() -> None:
    document = render_html(build_view(check(*many_references(30))))
    assert 'width="23" height="16"' in document and ">AA</text>" in document


# --- the diagram's relationship budget -----------------------------------------------------
def budget_case(reviews: int, deploys: int) -> tuple[Any, Any, Any]:
    catalog = tomllib.loads(CATALOG.read_text())
    catalog["actions"]["deploy"]["inputs"] = {}
    contract = tomllib.loads(CONTRACT.read_text())
    contract["required_actions"] = ["code_review", "deploy"]
    contract["rules"] = [r for r in contract["rules"] if r["id"] == "review-before-deploy"]
    steps = [{"id": f"r{i}", "action": "code_review"} for i in range(reviews)]
    steps += [{"id": f"d{i}", "action": "deploy"} for i in range(deploys)]
    return {"schema": "planlens-agent/plan/v1", "steps": steps}, contract, catalog


def test_over_budget_diagram_generates_no_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """The review's case: 40 x 40 relationships. Escaping is the first thing any path
    or node does, so one call here means work was done for a diagram nobody will see."""
    result = check(*budget_case(40, 40))
    assert result.passed
    view = build_view(result)
    assert inspector._relation_count(view) == 1600 > DIAGRAM_MAX_ARCS

    def no_formatting(value: object) -> str:
        raise AssertionError("formatted a diagram element for an over-budget diagram")

    monkeypatch.setattr(inspector, "e", no_formatting)
    fragment = inspector._diagram(view)
    assert "too many relationships" in fragment and "<path" not in fragment


def test_negative_control_within_budget_does_format() -> None:
    view = build_view(check(*budget_case(30, 50)))
    assert inspector._relation_count(view) == DIAGRAM_MAX_ARCS  # exactly at the limit
    assert inspector._diagram(view).count('class="arc pre') == DIAGRAM_MAX_ARCS


def test_one_over_the_limit_is_omitted_and_the_tables_remain() -> None:
    page = Page(render_html(build_view(check(*budget_case(31, 49)))))
    assert ("class", "diagramwrap") not in page.attrs_seen
    assert "too many relationships" in page.visible
    assert "every deploy needs an earlier code_review" in page.visible
    assert "code_review: steps[0], steps[1]" in page.visible and "d48" in page.visible


# --- raw evidence behind a disclosure ------------------------------------------------------
def parts_of(document: str, finding: str) -> tuple[str, str]:
    """(what is shown without opening anything, the raw disclosure) for one finding."""
    start = document.index(f'id="finding-{finding}"')
    following = document.find('<li class="finding"', start)
    block = document[start : following if following != -1 else document.index("</ol>", start)]
    if '<details class="raw">' not in block:
        return block, ""
    visible, raw = block.split('<details class="raw">', 1)
    return visible, raw


def test_raw_evidence_is_closed_by_default_and_complete() -> None:
    document = render_html(build_view(check(plan("artifact-mismatch.json"), CONTRACT, CATALOG)))
    assert " open" not in re.findall(r"<details[^>]*>", document)[0]
    visible, raw = parts_of(document, "f1")
    assert "Raw observed and required data</summary>" in raw
    assert "<pre>" not in visible and "<pre>" in raw
    assert "&quot;refers_to&quot;: &quot;build_hotfix.artifact&quot;" in raw
    assert "steps[5] deploy_prod input artifact &rarr; build_hotfix.artifact" in visible
    assert "one planned artifact across" in visible


def test_explanation_and_next_step_stay_outside_the_disclosure() -> None:
    result = check(plan("reversed-review.json"), CONTRACT, CATALOG)
    visible, raw = parts_of(render_html(build_view(result)), "f1")
    [d] = result.diagnostics
    assert "later occurrences: steps[4] review" in visible and "prior occurrences: none" in visible
    assert "Meaning</dt>" in visible and "Next</dt>" in visible and "Why</dt>" in visible
    assert "Meaning" not in raw and d.message.replace("'", "&#x27;") in visible


def test_compact_evidence_uses_structure_not_wording() -> None:
    result = check(plan("reversed-review.json"), CONTRACT, CATALOG)
    blanked = dataclasses.replace(
        result,
        diagnostics=tuple(
            dataclasses.replace(d, message="", suggestion="") for d in result.diagnostics
        ),
    )
    visible, _ = parts_of(render_html(build_view(blanked)), "f1")
    assert "later occurrences: steps[4] review" in visible


def test_scalar_evidence_needs_no_disclosure() -> None:
    p = plan("valid.json")
    p["waive"] = ["review-before-deploy"]
    visible, raw = parts_of(render_html(build_view(check(p, CONTRACT, CATALOG))), "f1")
    assert raw == "" and "Observed</dt><dd>waive</dd>" in visible


def test_long_evidence_is_shortened_visibly_and_kept_whole_in_the_disclosure() -> None:
    document = render_html(build_view(check(*many_references(30))))
    visible, raw = parts_of(document, "f1")
    assert f"and {31 - COMPACT_ITEMS} more" in visible
    assert "u29" not in visible.split("Contract rule")[0]
    assert "u29" in raw and "31 related locations" not in raw
    assert "related locations</summary>" in visible


def test_compact_evidence_escapes_input_values() -> None:
    p = plan("valid.json")
    p["steps"][0]["id"] = {"<img src=x onerror=window.pwned=5>": ["</script><script>"]}
    document = render_html(build_view(check(p, CONTRACT, CATALOG)))
    page = Page(document)
    assert "img" not in page.tags and page.tags.count("script") == 2
