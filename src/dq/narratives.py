"""Owner narratives via Snowflake Cortex (brief §10.4).

Only aggregated facts ever reach the model: per-rule counts, change versus
the previous run, new/resolved totals, site *names* with their critical and
high counts, the oldest open findings as (rule, site, age), and source
freshness. Never `DETAIL`, record names or any free text from source
records -- the tests enforce it. The exact input is stored alongside each
narrative (`DQ_NARRATIVE.INPUT_SUMMARY`) so what was sent can be audited.

The model call sits behind `NarrativeCompleter`, separate from the engine's
`RuleExecutor`, so tests and the app can substitute a fake. A failed
narrative is recorded as ERRORED and never raised -- it must not fail a run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from dq.store.base import GovernanceStore

DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
DEFAULT_PROMPT_VERSION = "v1"
_TOP_SITES = 10
_OLDEST_FINDINGS = 5
_SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
_NZ = ZoneInfo("Pacific/Auckland")


class NarrativeCompleter(Protocol):
    def complete(self, model: str, prompt: str, *, query_tag: str) -> str: ...


@dataclass(frozen=True)
class NarrativeResult:
    owner_email: str
    status: str  # SUCCEEDED | ERRORED
    narrative: str | None = None
    error: str | None = None


def _hours_before(run_at: datetime | None, synced: datetime | None) -> float | None:
    if run_at is None or synced is None:
        return None
    return round((run_at - synced).total_seconds() / 3600, 1)


def _rule_entries(
    store: GovernanceStore, run_id: str, owner_email: str
) -> tuple[list[dict[str, Any]], datetime | None]:
    by_rule: dict[str, list[dict[str, Any]]] = {}
    for row in store.get_rule_trend(runs=13):
        if row["OWNER_EMAIL"] == owner_email:
            by_rule.setdefault(row["RULE_CODE"], []).append(row)

    entries: list[dict[str, Any]] = []
    run_at: datetime | None = None
    for code in sorted(by_rule):
        history = sorted(by_rule[code], key=lambda r: r["RUN_AT"])
        position = next((i for i, r in enumerate(history) if r["RUN_ID"] == run_id), None)
        if position is None:
            continue
        current = history[position]
        previous = history[position - 1] if position > 0 else None
        run_at = current["RUN_AT"]
        entries.append(
            {
                "code": code,
                "name": current["RULE_NAME"],
                "severity": current["SEVERITY"],
                "dimension": current["DIMENSION"],
                "record_label": current["RECORD_LABEL"],
                "records_evaluated": current["RECORDS_EVALUATED"],
                "exceptions": current["RECORDS_FAILED_OPEN"],
                "exception_rate": float(current["EXCEPTION_RATE"] or 0),
                "previous_exceptions": previous["RECORDS_FAILED_OPEN"] if previous else None,
                "change": (
                    current["RECORDS_FAILED_OPEN"] - previous["RECORDS_FAILED_OPEN"]
                    if previous
                    else None
                ),
            }
        )
    return entries, run_at


def build_input(store: GovernanceStore, run_id: str, owner_email: str) -> dict[str, Any]:
    """The aggregated facts for one owner and one run (brief §10.4 step 1)."""
    rules, run_at = _rule_entries(store, run_id, owner_email)
    findings = store.list_findings(owner_email)
    open_findings = [f for f in findings if f["STATUS"] == "OPEN"]

    open_by_severity = {
        severity: count
        for severity in _SEVERITY_ORDER
        if (count := sum(1 for f in open_findings if f["SEVERITY"] == severity))
    }

    per_site: dict[str, dict[str, int]] = {}
    for finding in open_findings:
        if finding["SEVERITY"] in ("CRITICAL", "HIGH") and finding["SITE_NAME"]:
            counts = per_site.setdefault(finding["SITE_NAME"], {"critical": 0, "high": 0})
            counts[finding["SEVERITY"].lower()] += 1
    top_sites = [
        {"site": site, **counts}
        for site, counts in sorted(
            per_site.items(), key=lambda kv: (-kv[1]["critical"], -kv[1]["high"], kv[0])
        )[:_TOP_SITES]
    ]

    oldest = sorted(open_findings, key=lambda f: (-(f["AGE_DAYS"] or 0), f["FINDING_ID"]))
    oldest_open = [
        {"rule": f["RULE_CODE"], "site": f["SITE_NAME"], "age_days": f["AGE_DAYS"]}
        for f in oldest[:_OLDEST_FINDINGS]
    ]

    owner = store.get_owner(owner_email)
    owned_codes = {r["RULE_CODE"] for r in store.list_rules() if r["OWNER_EMAIL"] == owner_email}
    errored = [
        e["RULE_CODE"] for e in store.get_run_rule_errors(run_id) if e["RULE_CODE"] in owned_codes
    ]

    return {
        "owner": owner["DISPLAY_NAME"] if owner else owner_email,
        # The date as owners read it (NZ), not UTC, so the narrative never
        # says "yesterday" for a run that happened this morning.
        "run_date": run_at.astimezone(_NZ).date().isoformat() if run_at else None,
        "rules": rules,
        "errored_rules": errored,
        "findings": {
            "new": sum(1 for f in findings if f["FIRST_SEEN_RUN_ID"] == run_id),
            "resolved": sum(1 for f in findings if f["RESOLVED_RUN_ID"] == run_id),
            "open_total": len(open_findings),
            "open_by_severity": open_by_severity,
        },
        "top_sites": top_sites,
        "oldest_open": oldest_open,
        "source_freshness": [
            {
                "source": row["SOURCE_NAME"],
                "synced_hours_before_run": _hours_before(run_at, row["LAST_SYNCED_AT"]),
            }
            for row in store.get_source_freshness_latest()
        ],
    }


def render_prompt(template: str, data: dict[str, Any]) -> str:
    return template.replace("{{ input_json }}", json.dumps(data, indent=2, default=str))


def generate(
    store: GovernanceStore,
    completer: NarrativeCompleter,
    owner_email: str,
    *,
    run_id: str,
    now: datetime | None = None,
    prompts_dir: Path | None = None,
    query_tag: str | None = None,
) -> NarrativeResult:
    """Build the input, call the model, store the result. Never raises: any
    failure is stored as an ERRORED narrative (brief §10.4 step 4)."""
    generated_at = now or datetime.now(UTC)
    model = store.get_config_value("CORTEX_MODEL")
    configured_version = store.get_config_value("NARRATIVE_PROMPT_VERSION")
    version = (
        configured_version
        if configured_version and not configured_version.startswith("<")
        else DEFAULT_PROMPT_VERSION
    )
    data: dict[str, Any] | None = None
    try:
        if not model or model.startswith("<"):
            raise ValueError("CORTEX_MODEL is not configured in DQ_CONFIG")
        template_path = (prompts_dir or DEFAULT_PROMPTS_DIR) / f"narrative_{version}.md"
        if not template_path.exists():
            raise FileNotFoundError(f"Prompt file not found: {template_path.name}")
        data = build_input(store, run_id, owner_email)
        prompt = render_prompt(template_path.read_text(), data)
        text = completer.complete(
            model, prompt, query_tag=query_tag or f"dq_poc:narrative:{run_id}:{owner_email}"
        )
        if text is None or not text.strip():
            raise ValueError("Cortex returned an empty narrative")
    except Exception as exc:
        store.write_narrative(
            run_id,
            owner_email,
            status="ERRORED",
            narrative=None,
            model=model,
            prompt_version=version,
            input_summary=data,
            generated_at=generated_at,
            error_message=str(exc),
        )
        return NarrativeResult(owner_email, "ERRORED", error=str(exc))

    store.write_narrative(
        run_id,
        owner_email,
        status="SUCCEEDED",
        narrative=text.strip(),
        model=model,
        prompt_version=version,
        input_summary=data,
        generated_at=generated_at,
    )
    return NarrativeResult(owner_email, "SUCCEEDED", narrative=text.strip())


def generate_all(
    store: GovernanceStore,
    completer: NarrativeCompleter,
    run_id: str,
    *,
    now: datetime | None = None,
    prompts_dir: Path | None = None,
) -> list[NarrativeResult]:
    """One narrative per active owner who owns at least one active rule."""
    with_active_rules = {rule["OWNER_EMAIL"] for rule in store.get_active_rules()}
    owners = sorted(
        o["OWNER_EMAIL"] for o in store.list_owners() if o["OWNER_EMAIL"] in with_active_rules
    )
    return [
        generate(store, completer, email, run_id=run_id, now=now, prompts_dir=prompts_dir)
        for email in owners
    ]
