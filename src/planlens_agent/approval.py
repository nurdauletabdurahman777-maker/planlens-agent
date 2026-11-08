"""Binding a passing check to the exact payload that may be executed.

An :class:`ApprovedPlan` holds a read-only deep snapshot of the plan that was
checked, its digest, and the digests of the contract and catalog it passed
under. A runner should execute the snapshot - never a payload supplied
separately - and should refuse when any of those digests disagree with what
it holds.

Limits, stated plainly:

* SHA-256 digests identify content. They do not show who produced it, that a
  human approved anything, or that anything ran.
* This is a library. A caller who never calls it, or who executes some other
  object, is not stopped by it.
* Anyone can construct an ``ApprovedPlan`` by hand. The reference runner
  therefore re-checks the snapshot under its own trusted policy before
  dispatch; see ``runner.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .loading import digest, freeze, thaw


class NotApproved(Exception):
    """Raised by ``CheckResult.approve()`` when the plan did not pass."""

    def __init__(self, verdict: str, findings: int) -> None:
        super().__init__(
            f"plan not approved: verdict is {verdict!r} with {findings} finding(s); "
            "a rejected or unevaluated plan cannot be dispatched"
        )
        self.verdict = verdict
        self.findings = findings


@dataclass(frozen=True)
class ApprovedPlan:
    plan: Mapping[str, Any]
    plan_digest: str
    contract_id: str
    contract_version: str
    contract_digest: str
    catalog_id: str
    catalog_version: str
    catalog_digest: str
    engine_version: str

    def __post_init__(self) -> None:
        # Always hold a private read-only copy, also when built by hand or with
        # dataclasses.replace(): a caller keeping a reference to the mapping they
        # passed in cannot change what this approval holds.
        object.__setattr__(self, "plan", freeze(thaw(self.plan)))

    def integrity_ok(self) -> bool:
        """True when the held snapshot still has the digest it was approved with."""
        try:
            return digest(self.plan) == self.plan_digest
        except (TypeError, ValueError):
            return False

    def binding(self) -> dict[str, str]:
        """The identifying fields, without the plan body."""
        return {
            "plan_sha256": self.plan_digest,
            "contract": f"{self.contract_id} {self.contract_version}",
            "contract_sha256": self.contract_digest,
            "catalog": f"{self.catalog_id} {self.catalog_version}",
            "catalog_sha256": self.catalog_digest,
            "engine_version": self.engine_version,
        }
