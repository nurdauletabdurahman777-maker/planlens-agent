"""A thin reference runner that dispatches only an approved snapshot.

This is an example of binding a check to execution, not an orchestration
engine. It calls one registered Python callable per step, in plan order, and
passes each step the values earlier handlers returned for the outputs it
references. There are no retries, no parallelism and no repairs.

Before calling the first handler, :meth:`ReferenceRunner.dispatch` refuses -
dispatching nothing - when any of these fail:

0. the runner's own policy still matches what it validated;
1. the approval is an :class:`ApprovedPlan` (a rejected ``CheckResult`` is not);
2. the approval's snapshot still has the digest it was approved with;
3. if an execution payload is presented, its digest equals the approved digest.
   A changed payload is blocked until it is checked again and a new approval
   is issued; the runner never re-validates a substituted payload itself;
4. the approval was issued under the same contract, catalog and engine version
   this runner holds;
5. the snapshot passes a fresh check under this runner's own trusted policy,
   so a hand-built approval for an unchecked plan is still refused;
6. every action in the snapshot has a registered handler.

What is executed is the runner's own read-only copy of the plan, made by the
re-check in step 5 and verified to have the approved digest. Neither a
presented payload nor the approval's ``plan`` attribute is read during
dispatch, so changing either once dispatch has begun has no effect.

None of this stops a caller who executes plans without the runner. Digests
identify content; they do not establish who produced it, that anyone approved
it, or what the handlers actually did.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from ._version import __version__
from .approval import ApprovedPlan
from .loading import Source, digest
from .policy import validate_policy


@dataclass(frozen=True)
class StepCall:
    """What a handler receives: the read-only step and its resolved input values."""

    step_id: str
    action: str
    step: Mapping[str, Any]
    inputs: Mapping[str, Any]


Handler = Callable[[StepCall], Mapping[str, Any] | None]


class DispatchBlocked(Exception):
    """Raised before any handler runs. ``dispatched`` is always 0."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"dispatch blocked: {reason}")
        self.reason = reason
        self.dispatched = 0


@dataclass
class DispatchRecord:
    status: Literal["completed", "handler_failed"]
    dispatched: list[tuple[str, str]] = field(default_factory=list)
    failed_step: str | None = None
    error: str | None = None
    binding: dict[str, str] = field(default_factory=dict)


class ReferenceRunner:
    def __init__(self, contract: Source, catalog: Source, handlers: Mapping[str, Handler]) -> None:
        policy = validate_policy(contract, catalog)
        if (
            not policy.usable
            or policy.diagnostics
            or policy.catalog is None
            or policy.contract is None
        ):
            ids = sorted({d.rule_id for d in policy.diagnostics})
            raise ValueError(f"runner refuses a policy it cannot fully enforce: {ids}")
        unknown = sorted(set(handlers) - set(policy.catalog.actions))
        if unknown:
            raise ValueError(f"handlers registered for actions not in the catalog: {unknown}")
        self.policy = policy
        self.handlers = dict(handlers)

    def dispatch(self, approval: object, payload: object = None) -> DispatchRecord:
        from .checker import check

        if not isinstance(approval, ApprovedPlan):
            raise DispatchBlocked(
                f"expected an ApprovedPlan, got {type(approval).__name__}; "
                "only a plan that passed can be dispatched"
            )
        if not self.policy.verify():
            raise DispatchBlocked(
                "this runner's policy no longer matches the catalog and contract it validated; "
                "construct a new runner from the changed documents"
            )
        if not approval.integrity_ok():
            raise DispatchBlocked("the approved snapshot no longer matches its approved digest")
        if payload is not None:
            try:
                presented = digest(payload)
            except (TypeError, ValueError) as exc:
                raise DispatchBlocked(f"the presented payload cannot be digested: {exc}") from None
            if presented != approval.plan_digest:
                raise DispatchBlocked(
                    f"the presented payload (sha256:{presented[:12]}) is not the approved plan "
                    f"(sha256:{approval.plan_digest[:12]}); check it again to obtain a new approval"
                )
        con, cat = self.policy.contract, self.policy.catalog
        assert con is not None and cat is not None
        if approval.contract_digest != con.digest:
            raise DispatchBlocked("the approval was issued under a different contract")
        if approval.catalog_digest != cat.digest:
            raise DispatchBlocked("the approval was issued under a different catalog")
        if approval.engine_version != __version__:
            raise DispatchBlocked(
                f"the approval was issued by engine {approval.engine_version}, "
                f"this is {__version__}"
            )

        recheck = check(approval.plan, policy=self.policy)
        if not recheck.passed:
            raise DispatchBlocked(
                f"the approved snapshot does not pass under this runner's policy "
                f"({recheck.verdict}: "
                f"{sorted({d.rule_id for d in recheck.diagnostics})})"
            )
        # Execute the runner's own independently owned, read-only snapshot - the one
        # it just validated - and never read approval.plan again. It must be the
        # approved content; anything else would let a replacement through.
        snapshot = recheck.plan_snapshot
        if snapshot is None or recheck.plan_digest != approval.plan_digest:
            raise DispatchBlocked("the plan changed while it was being re-checked")
        steps = snapshot["steps"]
        missing = sorted({s["action"] for s in steps} - set(self.handlers))
        if missing:
            raise DispatchBlocked(f"no handler registered for {missing}; nothing was dispatched")

        record = DispatchRecord("completed", binding=approval.binding())
        produced: dict[tuple[str, str], Any] = {}
        for s in steps:
            sid, action = s["id"], s["action"]
            inputs: dict[str, Any] = {}
            for name, ref in s.get("inputs", {}).items():
                producer_id, output = ref.split(".", 1)
                inputs[name] = produced[(producer_id, output)]
            record.dispatched.append((sid, action))
            try:
                result = self.handlers[action](StepCall(sid, action, s, inputs))
            except Exception as exc:  # stop; no retries
                record.status, record.failed_step = "handler_failed", sid
                record.error = f"{type(exc).__name__}: {exc}"
                return record
            declared = set(cat.actions[action].outputs)
            got = dict(result or {})
            if set(got) != declared:
                record.status, record.failed_step = "handler_failed", sid
                record.error = (
                    f"handler for {action!r} returned outputs {sorted(got)}, "
                    f"the catalog declares {sorted(declared)}"
                )
                return record
            for name, value in got.items():
                produced[(sid, name)] = value
        return record
