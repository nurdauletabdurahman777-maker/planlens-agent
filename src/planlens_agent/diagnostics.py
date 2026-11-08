"""The one diagnostic representation used by the SDK, the CLI and saved reports.

Every finding - a plan violation, an invalid document, or a requirement the
engine cannot evaluate - is a :class:`Diagnostic`. Rule metadata lives in
:data:`RULES`; ``planlens-agent explain`` and ``docs/rules.md`` are rendered from
it, so the offline explanation and the documentation cannot drift apart.

Locations are logical paths into the parsed document (``/steps/3/inputs/artifact``)
plus the step identity and its 0-based position in the plan. A plan position is
never presented as a file line. A source span is reported only when the parser
supplies one, which in this version means JSON and TOML syntax errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Category = Literal[
    "violation", "invalid_plan", "invalid_contract", "invalid_catalog", "unsupported"
]
Document = Literal["plan", "contract", "catalog", "suite"]

#: Display/precedence order of categories. Earlier entries dominate the verdict.
CATEGORY_ORDER: tuple[Category, ...] = (
    "invalid_catalog",
    "invalid_contract",
    "invalid_plan",
    "unsupported",
    "violation",
)


@dataclass(frozen=True)
class SourceSpan:
    """A position reported by a parser. Never derived from a plan index."""

    line: int
    column: int

    def to_dict(self) -> dict[str, int]:
        return {"line": self.line, "column": self.column}


@dataclass(frozen=True)
class Location:
    """Where a finding applies.

    ``path`` is a JSON-pointer-style logical path into the parsed document.
    ``step_index`` is the 0-based position in the plan's ``steps`` list, which
    is the proposed execution position; it is not a line number.
    """

    document: Document
    path: str
    step_id: str | None = None
    step_index: int | None = None
    source_span: SourceSpan | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "document": self.document,
            "path": self.path,
            "step_id": self.step_id,
            "step_index": self.step_index,
            "source_span": self.source_span.to_dict() if self.source_span else None,
        }


@dataclass(frozen=True)
class Diagnostic:
    rule_id: str
    message: str
    location: Location | None
    observed: Any = None
    required: Any = None
    suggestion: str = ""
    contract_rule: str | None = None
    location_absent_reason: str | None = None
    related: tuple[Location, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def rule(self) -> RuleInfo:
        return RULES[self.rule_id]

    @property
    def category(self) -> Category:
        return RULES[self.rule_id].category

    def to_dict(self) -> dict[str, Any]:
        info = RULES[self.rule_id]
        return {
            "rule_id": self.rule_id,
            "rule_version": info.version,
            "category": info.category,
            "title": info.title,
            "message": self.message,
            "observed": self.observed,
            "required": self.required,
            "location": self.location.to_dict() if self.location else None,
            "location_absent_reason": self.location_absent_reason,
            "related_locations": [loc.to_dict() for loc in self.related],
            "contract_rule": self.contract_rule,
            "explanation": info.summary,
            "suggestion": self.suggestion,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class RuleInfo:
    id: str
    version: int
    category: Category
    title: str
    summary: str
    bad_example: str = ""
    good_example: str = ""
    limitations: str = ""


RULES: dict[str, RuleInfo] = {}


def _rule(info: RuleInfo) -> None:
    if info.id in RULES:
        raise RuntimeError(f"duplicate rule id {info.id}")
    RULES[info.id] = info


# ---------------------------------------------------------------------------
# Document-level rules, one family per document kind.
# ---------------------------------------------------------------------------
_DOC_CATEGORY: dict[Document, Category] = {
    "plan": "invalid_plan",
    "contract": "invalid_contract",
    "catalog": "invalid_catalog",
}

for _doc in ("catalog", "contract", "plan"):
    _cat = _DOC_CATEGORY[_doc]
    _rule(
        RuleInfo(
            f"{_doc}/unreadable",
            1,
            _cat,
            f"{_doc.capitalize()} could not be read",
            f"The {_doc} file does not exist, is not a regular file, or cannot be read as UTF-8.",
            limitations="Nothing in the document is evaluated.",
        )
    )
    _rule(
        RuleInfo(
            f"{_doc}/malformed",
            1,
            _cat,
            f"{_doc.capitalize()} is not well-formed",
            f"The {_doc} is not valid JSON or TOML, or a JSON object repeats a key. Duplicate "
            "keys are rejected because different parsers keep different copies, so the "
            "document's meaning would be ambiguous.",
            bad_example='{"id": "a", "id": "b"}',
            limitations="A source span is given when the parser reports one.",
        )
    )
    _rule(
        RuleInfo(
            f"{_doc}/schema",
            1,
            _cat,
            f"{_doc.capitalize()} does not match its schema",
            f"A required field is missing, a field has the wrong type or pattern, or the {_doc} "
            "contains a field this schema version does not define. Unknown fields are "
            "rejected rather than ignored so that a misspelt requirement cannot vanish.",
        )
    )
    _rule(
        RuleInfo(
            f"{_doc}/limit",
            1,
            _cat,
            f"{_doc.capitalize()} exceeds a size limit",
            f"The {_doc} is larger than the documented bounds, which are checked before any "
            "expensive processing.",
        )
    )
    _rule(
        RuleInfo(
            f"{_doc}/unsupported-version",
            1,
            "unsupported",
            f"{_doc.capitalize()} schema version is not supported",
            f"The {_doc} declares a schema version this engine does not implement. It is "
            "refused rather than read with the wrong semantics.",
        )
    )

# ---------------------------------------------------------------------------
# Plan input rules that are not violations.
# ---------------------------------------------------------------------------
_rule(
    RuleInfo(
        "plan/duplicate-step-id",
        1,
        "invalid_plan",
        "Step identifiers are not unique",
        "Two steps share an id. Artifact references name a producer step by id, so a "
        "duplicate makes every reference to it ambiguous.",
        bad_example='[{"id": "build", ...}, {"id": "build", ...}]',
    )
)
_rule(
    RuleInfo(
        "plan/unsupported-semantics",
        1,
        "unsupported",
        "Plan uses semantics this engine does not support",
        "The plan asks for execution semantics outside sequential plans: parallel groups, "
        "conditions, loops, candidate-declared dependencies, or a non-sequential mode. The "
        "plan is not evaluated, because checking it as a sequential list could report a "
        "pass for a plan that would run differently.",
        bad_example='{"id": "deploy", "action": "deploy", "when": "tests_passed"}',
        limitations="Execution order is the list order. Requirements come only from the "
        "owner contract, never from the candidate.",
    )
)

# ---------------------------------------------------------------------------
# Plan violations: the plan parsed, but it does not satisfy the catalog/contract.
# ---------------------------------------------------------------------------
_rule(
    RuleInfo(
        "plan/unknown-action",
        1,
        "violation",
        "Step uses an action the catalog does not define",
        "Each step must name an action from the owner's catalog. An unknown action has no "
        "trusted definition, so the step cannot be checked or dispatched.",
        bad_example='{"id": "s1", "action": "deploy_everything"}',
        good_example='{"id": "s1", "action": "deploy", ...}',
    )
)
_rule(
    RuleInfo(
        "plan/missing-required-action",
        1,
        "violation",
        "A required action is absent",
        "The owner contract requires this action, and no step performs it. Leaving a "
        "requirement out of the plan does not remove it.",
        limitations="There is no plan location, because the missing step does not exist. "
        "The related location points at the requirement in the contract.",
    )
)
_rule(
    RuleInfo(
        "plan/prerequisite-order",
        1,
        "violation",
        "Action runs without a prior prerequisite",
        "A contract prerequisite says every step of one action must be preceded by a step of "
        "another. This step has no earlier step of the prerequisite action, either because "
        "it appears later or because it does not appear at all.",
        bad_example="build, unit_tests, deploy, code_review",
        good_example="build, unit_tests, code_review, deploy",
        limitations="Order is checked over the declared plan. It does not show that the "
        "prerequisite step succeeded or approved anything.",
    )
)
_rule(
    RuleInfo(
        "plan/unresolved-reference",
        1,
        "violation",
        "Artifact reference does not resolve",
        "An input refers to `<step_id>.<output>`, and either no step has that id, or that "
        "step's action does not declare that output in the catalog.",
        bad_example='"inputs": {"artifact": "build_rc.binary"}',
        good_example='"inputs": {"artifact": "build_rc.artifact"}',
    )
)
_rule(
    RuleInfo(
        "plan/reference-before-producer",
        1,
        "violation",
        "Artifact is consumed before it is produced",
        "An input refers to an output of a step at the same or a later position, so the "
        "artifact would not exist yet when this step runs.",
        bad_example="unit_tests (uses build_rc.artifact) placed before build_rc",
    )
)
_rule(
    RuleInfo(
        "plan/missing-input",
        1,
        "violation",
        "Required input is not bound",
        "The catalog declares this input for the action, and the step does not bind it to a "
        "producer output. All declared inputs are required in this schema version.",
    )
)
_rule(
    RuleInfo(
        "plan/unknown-input",
        1,
        "violation",
        "Input is not declared for this action",
        "The step binds an input name the catalog does not declare for the action.",
    )
)
_rule(
    RuleInfo(
        "plan/artifact-type",
        1,
        "violation",
        "Referenced artifact has the wrong type",
        "The referenced producer output has a different declared artifact type from the one "
        "this input accepts.",
    )
)
_rule(
    RuleInfo(
        "plan/artifact-identity",
        1,
        "violation",
        "Inputs that must share one artifact do not",
        "A contract rule says several inputs must refer to the same planned artifact - for "
        "example, the artifact that is deployed must be the one that was tested. These "
        "inputs resolve to different producer outputs.",
        bad_example="unit_tests uses build_a.artifact; deploy uses build_b.artifact",
        good_example="unit_tests and deploy both use build_a.artifact",
        limitations="Identity is the planned producer step and output name. It is not "
        "evidence that bytes exist, that tests passed, or what was deployed. "
        "The checker cannot tell which of the differing inputs is the wrong one.",
    )
)
_rule(
    RuleInfo(
        "plan/policy-override",
        1,
        "violation",
        "Plan tries to change trusted policy",
        "The plan contains a field that would waive a requirement, replace the contract or "
        "catalog, change their version, or declare what an action is or does. Those come "
        "only from the owner. The field is not honoured, and every requirement is still "
        "evaluated.",
        bad_example='{"waive": ["review-before-deploy"], "steps": [...]}',
    )
)

# ---------------------------------------------------------------------------
# Contract (owner policy) validity.
# ---------------------------------------------------------------------------
_rule(
    RuleInfo(
        "contract/unknown-action",
        1,
        "invalid_contract",
        "Contract names an action the catalog lacks",
        "A requirement refers to an action, or an action input, that is not in the catalog. "
        "It could never be satisfied, so the contract is refused before any plan is checked.",
    )
)
_rule(
    RuleInfo(
        "contract/catalog-mismatch",
        1,
        "invalid_contract",
        "Contract was written for another catalog",
        "The contract pins a catalog id and version, and the supplied catalog differs. "
        "Checking against a different catalog could silently change what the rules mean.",
    )
)
_rule(
    RuleInfo(
        "contract/cycle",
        1,
        "invalid_contract",
        "Prerequisites form a cycle",
        "Prerequisite rules form a cycle, so no plan containing any action on the cycle can "
        "satisfy them. The contract is inconsistent.",
        bad_example="deploy requires prior review; review requires prior deploy",
    )
)
_rule(
    RuleInfo(
        "contract/inconsistent-artifact-rule",
        1,
        "invalid_contract",
        "Artifact-identity rule can never be satisfied",
        "A same-artifact rule lists inputs that accept different artifact types, so no single "
        "planned artifact can satisfy all of them.",
    )
)
_rule(
    RuleInfo(
        "contract/unsatisfiable-input",
        2,
        "invalid_contract",
        "Required action can never run",
        "A required action takes an input that no finite sequential plan can produce "
        "before it: either no catalog action outputs that type, or every action that does "
        "needs, directly or through others, an input that cannot be produced first. Every "
        "plan would fail, so the contract and catalog are inconsistent.",
        bad_example="produce: inputs seed=artifact, outputs result=artifact; nothing else",
        good_example="add fetch_seed: outputs seed=artifact, which needs no inputs",
        limitations="Prerequisite rules are not taken into account, so a required action "
        "that can only be preceded by an unrunnable prerequisite is not reported here; "
        "plans will still fail it.",
    )
)
_rule(
    RuleInfo(
        "contract/unsupported-rule",
        1,
        "unsupported",
        "Contract requires a rule kind this engine does not support",
        "The contract contains a rule whose kind this engine does not implement - for "
        "example, verifying approval evidence. Every rule in a contract is required, so the "
        "check reports that it cannot be evaluated instead of passing without it. Supported "
        "rules are still evaluated and reported.",
        bad_example='[[rules]]\nid = "approval-recorded"\nkind = "approval_evidence"',
        limitations="Supported kinds in this version: prerequisite, same_artifact.",
    )
)
