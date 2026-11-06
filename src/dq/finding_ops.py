"""Finding dispositions and the rule review flag (brief §10.2, §10.6 page 4).

Plain-module rules sitting on top of the GovernanceStore: what a
disposition needs, who may set one, and the all-or-nothing handling of a
bulk selection. The Streamlit Findings page calls these; nothing here
touches Snowflake or returns anything beyond the store's own metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from dq.store.base import GovernanceStore

DEFAULT_EXCEPTION_DAYS = 365
EXPIRING_SOON_DAYS = 30


class FindingOpsError(Exception):
    """A request the caller can't retry its way out of: a missing reason, a
    finding the owner doesn't own, a resolved finding, and so on."""


@dataclass(frozen=True)
class DispositionResult:
    count: int
    disposition: str
    expires_at: datetime | None


@dataclass(frozen=True)
class FindingSummary:
    open: int
    accepted: int
    expiring_soon: int  # accepted exceptions lapsing within EXPIRING_SOON_DAYS
    expired: int  # accepted exceptions already lapsed -- reopen at the next run
    false_positive: int
    resolved: int


def _require_reason(reason: str) -> str:
    if not reason or not reason.strip():
        raise FindingOpsError("A reason is required")
    return reason.strip()


def _can_act_on(owner: dict[str, Any], rule_owner_email: str) -> bool:
    return bool(owner.get("IS_APPROVER")) or owner["OWNER_EMAIL"] == rule_owner_email


def _load_findings(
    store: GovernanceStore, finding_ids: list[str], owner: dict[str, Any]
) -> list[dict[str, Any]]:
    unique_ids = list(dict.fromkeys(finding_ids))
    if not unique_ids:
        raise FindingOpsError("Select at least one finding")

    findings = []
    for finding_id in unique_ids:
        finding = store.get_finding(finding_id)
        if finding is None:
            raise FindingOpsError(f"Unknown finding: {finding_id}")
        if not _can_act_on(owner, finding["OWNER_EMAIL"]):
            raise FindingOpsError(
                f"You don't own the rule {finding['RULE_CODE']} -- only its owner or an "
                "approver can set a disposition on its findings"
            )
        findings.append(finding)
    return findings


def _exception_expiry(
    store: GovernanceStore, expires_at: datetime | None, now: datetime
) -> datetime:
    if expires_at is not None:
        if expires_at <= now:
            raise FindingOpsError("The expiry must be in the future")
        return expires_at
    configured = store.get_config_value("EXCEPTION_DEFAULT_DAYS")
    try:
        days = int(configured) if configured else DEFAULT_EXCEPTION_DAYS
    except ValueError:
        days = DEFAULT_EXCEPTION_DAYS
    return now + timedelta(days=days)


def _apply(
    store: GovernanceStore,
    finding_ids: list[str],
    disposition: str,
    reason: str,
    *,
    owner: dict[str, Any],
    facilitated_by: str | None,
    expires_at: datetime | None,
) -> DispositionResult:
    findings = _load_findings(store, finding_ids, owner)

    if disposition == "REVOKED":
        for finding in findings:
            if finding["STATUS"] not in ("ACCEPTED_EXCEPTION", "FALSE_POSITIVE"):
                raise FindingOpsError(
                    f"{finding['RULE_CODE']} / {finding['RECORD_NAME'] or finding['RECORD_ID']} "
                    f"is {finding['STATUS']} -- nothing to revoke"
                )
    else:
        for finding in findings:
            if finding["STATUS"] == "RESOLVED":
                raise FindingOpsError(
                    f"{finding['RULE_CODE']} / {finding['RECORD_NAME'] or finding['RECORD_ID']} "
                    "is already resolved -- there's nothing left to accept or dismiss"
                )

    count = store.set_dispositions(
        [f["FINDING_ID"] for f in findings],
        disposition,
        reason,
        set_by=owner["OWNER_EMAIL"],
        facilitated_by=facilitated_by,
        expires_at=expires_at,
    )
    return DispositionResult(count=count, disposition=disposition, expires_at=expires_at)


def accept_exception(
    store: GovernanceStore,
    finding_ids: list[str],
    reason: str,
    *,
    owner: dict[str, Any],
    facilitated_by: str | None = None,
    expires_at: datetime | None = None,
    now: datetime | None = None,
) -> DispositionResult:
    """Mark findings ACCEPTED_EXCEPTION until `expires_at` (default: now +
    EXCEPTION_DEFAULT_DAYS). They reopen by themselves at the next run after
    that date (brief §10.2)."""
    reason = _require_reason(reason)
    expiry = _exception_expiry(store, expires_at, now or datetime.now(UTC))
    return _apply(
        store,
        finding_ids,
        "ACCEPTED_EXCEPTION",
        reason,
        owner=owner,
        facilitated_by=facilitated_by,
        expires_at=expiry,
    )


def mark_false_positive(
    store: GovernanceStore,
    finding_ids: list[str],
    reason: str,
    *,
    owner: dict[str, Any],
    facilitated_by: str | None = None,
    now: datetime | None = None,
) -> DispositionResult:
    """Mark findings FALSE_POSITIVE -- the rule flagged something it
    shouldn't have. No expiry; the page then offers to flag the rule."""
    reason = _require_reason(reason)
    return _apply(
        store,
        finding_ids,
        "FALSE_POSITIVE",
        reason,
        owner=owner,
        facilitated_by=facilitated_by,
        expires_at=None,
    )


def revoke_disposition(
    store: GovernanceStore,
    finding_ids: list[str],
    reason: str,
    *,
    owner: dict[str, Any],
    facilitated_by: str | None = None,
    now: datetime | None = None,
) -> DispositionResult:
    """Undo an accepted exception or false positive: the finding is OPEN
    again, and the disposition row stays as history."""
    reason = _require_reason(reason)
    return _apply(
        store,
        finding_ids,
        "REVOKED",
        reason,
        owner=owner,
        facilitated_by=facilitated_by,
        expires_at=None,
    )


# --- rule review flag --------------------------------------------------------------


def _load_rule_for(store: GovernanceStore, rule_code: str, owner: dict[str, Any]) -> dict[str, Any]:
    rule = store.get_rule(rule_code)
    if rule is None:
        raise FindingOpsError(f"Unknown rule: {rule_code}")
    if not _can_act_on(owner, rule["OWNER_EMAIL"]):
        raise FindingOpsError(
            f"You don't own {rule_code} -- only its owner or an approver can change its flag"
        )
    return rule


def flag_rule_for_review(
    store: GovernanceStore,
    rule_code: str,
    reason: str,
    *,
    owner: dict[str, Any],
    facilitated_by: str | None = None,
) -> None:
    reason = _require_reason(reason)
    _load_rule_for(store, rule_code, owner)
    store.flag_rule_for_review(
        rule_code, reason, flagged_by=owner["OWNER_EMAIL"], facilitated_by=facilitated_by
    )


def clear_review_flag(
    store: GovernanceStore,
    rule_code: str,
    *,
    owner: dict[str, Any],
    facilitated_by: str | None = None,
    comment: str | None = None,
) -> None:
    _load_rule_for(store, rule_code, owner)
    store.clear_rule_review_flag(
        rule_code, cleared_by=owner["OWNER_EMAIL"], facilitated_by=facilitated_by, comment=comment
    )


# --- summary for the page header ---------------------------------------------------


def is_lapsing(finding: dict[str, Any], *, now: datetime | None = None) -> bool:
    """An accepted exception that has lapsed or lapses within EXPIRING_SOON_DAYS."""
    if finding["STATUS"] != "ACCEPTED_EXCEPTION":
        return False
    expires_at = finding.get("DISPOSITION_EXPIRES_AT")
    if expires_at is None:
        return False
    return expires_at <= (now or datetime.now(UTC)) + timedelta(days=EXPIRING_SOON_DAYS)


def summarise(findings: list[dict[str, Any]], *, now: datetime | None = None) -> FindingSummary:
    now = now or datetime.now(UTC)
    soon = now + timedelta(days=EXPIRING_SOON_DAYS)
    open_ = accepted = expiring = expired = false_positive = resolved = 0
    for finding in findings:
        status = finding["STATUS"]
        if status == "OPEN":
            open_ += 1
        elif status == "ACCEPTED_EXCEPTION":
            accepted += 1
            expires_at = finding.get("DISPOSITION_EXPIRES_AT")
            if expires_at is not None:
                if expires_at <= now:
                    expired += 1
                elif expires_at <= soon:
                    expiring += 1
        elif status == "FALSE_POSITIVE":
            false_positive += 1
        elif status == "RESOLVED":
            resolved += 1
    return FindingSummary(
        open=open_,
        accepted=accepted,
        expiring_soon=expiring,
        expired=expired,
        false_positive=false_positive,
        resolved=resolved,
    )
