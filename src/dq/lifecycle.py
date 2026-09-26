"""Finding lifecycle transitions (brief §10.2).

Pure decision logic, no store/DuckDB access: given a finding's current
state and this run's verdict for it, decide what should happen. The store
turns a decision into the actual DuckDB MERGE/UPDATE.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum


class FindingStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    ACCEPTED_EXCEPTION = "ACCEPTED_EXCEPTION"
    FALSE_POSITIVE = "FALSE_POSITIVE"


class DispositionType(StrEnum):
    """An *active* disposition's type (brief §10.2). REVOKED is never active."""

    ACCEPTED_EXCEPTION = "ACCEPTED_EXCEPTION"
    FALSE_POSITIVE = "FALSE_POSITIVE"


class ResolutionReason(StrEnum):
    FIXED = "FIXED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    RULE_RETIRED = "RULE_RETIRED"


class Verdict(StrEnum):
    """This run's outcome for one (record, item) under one successful rule."""

    FAILED = "FAILED"
    PASSED = "PASSED"
    NOT_EVALUATED = "NOT_EVALUATED"  # record/item no longer in the rule's scope


class Action(StrEnum):
    INSERT_OPEN = "INSERT_OPEN"
    UPDATE_LAST_SEEN = "UPDATE_LAST_SEEN"
    REOPEN = "REOPEN"
    APPLY_DISPOSITION = "APPLY_DISPOSITION"
    REACTIVATE = "REACTIVATE"
    RESOLVE_FIXED = "RESOLVE_FIXED"
    RESOLVE_OUT_OF_SCOPE = "RESOLVE_OUT_OF_SCOPE"
    CLOSE_RULE_RETIRED = "CLOSE_RULE_RETIRED"
    NO_CHANGE = "NO_CHANGE"


@dataclass(frozen=True)
class LifecycleDecision:
    action: Action
    new_status: FindingStatus | None
    resolution_reason: ResolutionReason | None = None
    increment_times_seen: bool = False
    increment_reopened_count: bool = False
    clear_resolved_fields: bool = False


_NO_CHANGE = LifecycleDecision(action=Action.NO_CHANGE, new_status=None)


def decide(
    existing_status: FindingStatus | None,
    active_disposition: DispositionType | None,
    verdict: Verdict,
) -> LifecycleDecision:
    """Decide the lifecycle action for one (record, item) under a rule that
    ran successfully this run (brief §10.2's table, row by row)."""
    if verdict == Verdict.FAILED:
        if existing_status is None:
            return LifecycleDecision(action=Action.INSERT_OPEN, new_status=FindingStatus.OPEN)
        if active_disposition is not None:
            was_resolved = existing_status == FindingStatus.RESOLVED
            return LifecycleDecision(
                action=Action.APPLY_DISPOSITION,
                new_status=FindingStatus(active_disposition.value),
                increment_reopened_count=was_resolved,
                clear_resolved_fields=was_resolved,
            )
        if existing_status == FindingStatus.OPEN:
            return LifecycleDecision(
                action=Action.UPDATE_LAST_SEEN,
                new_status=FindingStatus.OPEN,
                increment_times_seen=True,
            )
        if existing_status == FindingStatus.RESOLVED:
            return LifecycleDecision(
                action=Action.REOPEN,
                new_status=FindingStatus.OPEN,
                increment_reopened_count=True,
                clear_resolved_fields=True,
            )
        # ACCEPTED_EXCEPTION / FALSE_POSITIVE with no active disposition:
        # the disposition has expired or been revoked.
        return LifecycleDecision(action=Action.REACTIVATE, new_status=FindingStatus.OPEN)

    if verdict == Verdict.PASSED:
        if existing_status in (None, FindingStatus.RESOLVED):
            return _NO_CHANGE
        return LifecycleDecision(
            action=Action.RESOLVE_FIXED,
            new_status=FindingStatus.RESOLVED,
            resolution_reason=ResolutionReason.FIXED,
        )

    if verdict == Verdict.NOT_EVALUATED:
        if existing_status in (None, FindingStatus.RESOLVED):
            return _NO_CHANGE
        return LifecycleDecision(
            action=Action.RESOLVE_OUT_OF_SCOPE,
            new_status=FindingStatus.RESOLVED,
            resolution_reason=ResolutionReason.OUT_OF_SCOPE,
        )

    raise ValueError(f"Unknown verdict: {verdict}")


def decide_retired(existing_status: FindingStatus | None) -> LifecycleDecision:
    """Decide the action for a finding whose rule was retired since the last run."""
    if existing_status in (None, FindingStatus.RESOLVED):
        return _NO_CHANGE
    return LifecycleDecision(
        action=Action.CLOSE_RULE_RETIRED,
        new_status=FindingStatus.RESOLVED,
        resolution_reason=ResolutionReason.RULE_RETIRED,
    )


def compute_finding_id(rule_code: str, record_id: str, item_key: str | None) -> str:
    """FINDING_ID = sha256(RULE_CODE + '|' + RECORD_ID + '|' + (ITEM_KEY or '')) (brief §10.2)."""
    return hashlib.sha256(f"{rule_code}|{record_id}|{item_key or ''}".encode()).hexdigest()


def build_record_url(sf_base_url: str, sf_object: str, record_id: str) -> str:
    """RECORD_URL = SF_BASE_URL + '/lightning/r/' + sf_object + '/' + RECORD_ID + '/view'.

    Brief §10.2.
    """
    return f"{sf_base_url.rstrip('/')}/lightning/r/{sf_object}/{record_id}/view"
