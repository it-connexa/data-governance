"""Totals, rollups and site scoring (brief §10.3).

Pure arithmetic over already-evaluated rows plus disposition state supplied
by the caller. No store/DuckDB access, no Snowflake.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvaluatedRow:
    """One row of a successful rule's evaluated result, enriched with
    whether its finding currently carries an active disposition."""

    record_id: str
    item_key: str | None
    site_id: str | None
    is_valid: bool
    has_active_disposition: bool = False


@dataclass(frozen=True)
class RuleTotals:
    records_evaluated: int
    records_failed: int
    records_failed_open: int
    pass_rate: float | None
    items_evaluated: int
    items_failed: int
    items_failed_open: int
    item_pass_rate: float | None


def _pass_rate(evaluated: int, failed_open: int) -> float | None:
    if evaluated == 0:
        return None
    return round(100.0 * (evaluated - failed_open) / evaluated, 2)


def rule_totals(rows: list[EvaluatedRow]) -> RuleTotals:
    """Per-rule, per-run totals (brief §10.3's DQ_RULE_RESULT fields)."""
    records_evaluated = {r.record_id for r in rows}
    records_failed = {r.record_id for r in rows if not r.is_valid}
    records_failed_open = {
        r.record_id for r in rows if not r.is_valid and not r.has_active_disposition
    }

    items_evaluated = len(rows)
    items_failed = sum(1 for r in rows if not r.is_valid)
    items_failed_open = sum(1 for r in rows if not r.is_valid and not r.has_active_disposition)

    return RuleTotals(
        records_evaluated=len(records_evaluated),
        records_failed=len(records_failed),
        records_failed_open=len(records_failed_open),
        pass_rate=_pass_rate(len(records_evaluated), len(records_failed_open)),
        items_evaluated=items_evaluated,
        items_failed=items_failed,
        items_failed_open=items_failed_open,
        item_pass_rate=_pass_rate(items_evaluated, items_failed_open),
    )


@dataclass(frozen=True)
class ObjectResult:
    target_object: str
    record_label: str
    object_total: int | None
    active_rules: int
    rules_succeeded: int
    records_evaluated: int
    records_with_exception: int
    open_findings: int
    exception_rate: float | None


def object_rollup(
    *,
    target_object: str,
    record_label: str,
    object_total: int | None,
    active_rules: int,
    rules_succeeded: int,
    rows_by_rule: list[list[EvaluatedRow]],
) -> ObjectResult:
    """Per-object, per-run rollup (brief §10.3's DQ_OBJECT_RESULT fields).

    `rows_by_rule` holds the evaluated rows of each *successful* rule
    targeting this object, one list per rule.
    """
    records_evaluated: set[str] = set()
    records_with_exception: set[str] = set()
    open_findings = 0

    for rows in rows_by_rule:
        for row in rows:
            records_evaluated.add(row.record_id)
            if not row.is_valid and not row.has_active_disposition:
                records_with_exception.add(row.record_id)
                open_findings += 1

    exception_rate = None
    if records_evaluated:
        exception_rate = round(100.0 * len(records_with_exception) / len(records_evaluated), 2)

    return ObjectResult(
        target_object=target_object,
        record_label=record_label,
        object_total=object_total,
        active_rules=active_rules,
        rules_succeeded=rules_succeeded,
        records_evaluated=len(records_evaluated),
        records_with_exception=len(records_with_exception),
        open_findings=open_findings,
        exception_rate=exception_rate,
    )


@dataclass(frozen=True)
class SiteRuleRows:
    """One SCOPE='SITE' rule that succeeded this run, and its evaluated rows."""

    rule_code: str
    severity: str
    rows: list[EvaluatedRow]


@dataclass(frozen=True)
class SiteScore:
    site_id: str
    rules_applicable: int
    rules_failed: int
    open_findings: int
    critical_open: int
    weighted_score: float | None


def site_scores(
    severity_weights: dict[str, int],
    site_rule_rows: list[SiteRuleRows],
) -> dict[str, SiteScore]:
    """Per-site weighted scoring (brief §10.3's site scoring section).

    A rule is applicable to a site if the site appears in that rule's rows.
    A rule is failed for a site if it has >=1 open (non-dispositioned)
    failing row for that site.
    """
    applicable: dict[str, set[str]] = {}
    failed: dict[str, set[str]] = {}
    open_findings: dict[str, int] = {}
    critical_open: dict[str, int] = {}

    for rule in site_rule_rows:
        sites_seen_failing: set[str] = set()
        for row in rule.rows:
            if row.site_id is None:
                continue
            applicable.setdefault(row.site_id, set()).add(rule.rule_code)
            if not row.is_valid and not row.has_active_disposition:
                sites_seen_failing.add(row.site_id)
                open_findings[row.site_id] = open_findings.get(row.site_id, 0) + 1
                if rule.severity == "CRITICAL":
                    critical_open[row.site_id] = critical_open.get(row.site_id, 0) + 1
        for site_id in sites_seen_failing:
            failed.setdefault(site_id, set()).add(rule.rule_code)

    scores: dict[str, SiteScore] = {}
    for site_id, applicable_rules in applicable.items():
        failed_rules = failed.get(site_id, set())
        applicable_weight = sum(
            severity_weights[r] for r in _rule_severities(site_rule_rows, applicable_rules)
        )
        failed_weight = sum(
            severity_weights[r] for r in _rule_severities(site_rule_rows, failed_rules)
        )

        weighted_score = None
        if applicable_weight > 0:
            weighted_score = round(100.0 * (1 - failed_weight / applicable_weight), 2)

        scores[site_id] = SiteScore(
            site_id=site_id,
            rules_applicable=len(applicable_rules),
            rules_failed=len(failed_rules),
            open_findings=open_findings.get(site_id, 0),
            critical_open=critical_open.get(site_id, 0),
            weighted_score=weighted_score,
        )
    return scores


def _rule_severities(site_rule_rows: list[SiteRuleRows], rule_codes: set[str]) -> list[str]:
    by_code = {rule.rule_code: rule.severity for rule in site_rule_rows}
    return [by_code[code] for code in rule_codes]
