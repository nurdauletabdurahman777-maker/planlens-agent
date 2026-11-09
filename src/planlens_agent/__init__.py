"""planlens-agent: lint AI-agent plans before they run.

Checks a structured, ordered plan against an action catalog and a contract
supplied independently by the application owner. Offline and deterministic:
no model calls, no network, no clock, no randomness.

Naming and registration status of ``planlens-agent``: see docs/naming.md.
"""

from ._version import __version__
from .approval import ApprovedPlan, NotApproved
from .checker import CheckResult, check, evaluate
from .compare import compare_runs
from .diagnostics import RULES, Diagnostic, Location, RuleInfo, SourceSpan
from .fixtures import FixtureFileError, FixtureRun, run_fixtures
from .inspector import render_html
from .policy import Policy, PolicyIntegrityError, validate_policy
from .runner import DispatchBlocked, DispatchRecord, ReferenceRunner, StepCall
from .sarif import to_sarif, to_sarif_json
from .suite import RunFile, RunFileError, SuiteFileError, load_run, load_suite, run_suite
from .view_model import build_view

__all__ = [
    "RULES",
    "ApprovedPlan",
    "CheckResult",
    "Diagnostic",
    "DispatchBlocked",
    "DispatchRecord",
    "FixtureFileError",
    "FixtureRun",
    "Location",
    "NotApproved",
    "Policy",
    "PolicyIntegrityError",
    "ReferenceRunner",
    "RunFile",
    "RunFileError",
    "RuleInfo",
    "SourceSpan",
    "StepCall",
    "SuiteFileError",
    "__version__",
    "build_view",
    "check",
    "compare_runs",
    "evaluate",
    "load_run",
    "load_suite",
    "render_html",
    "run_fixtures",
    "run_suite",
    "to_sarif",
    "to_sarif_json",
    "validate_policy",
]
