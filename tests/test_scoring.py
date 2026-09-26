from dq.scoring import EvaluatedRow, SiteRuleRows, object_rollup, rule_totals, site_scores

# --- rule_totals ----------------------------------------------------------------


def test_single_item_rule_totals():
    rows = [
        EvaluatedRow("r1", None, "s1", True),
        EvaluatedRow("r2", None, "s1", False),
        EvaluatedRow("r3", None, "s2", True),
    ]

    totals = rule_totals(rows)

    assert totals.records_evaluated == 3
    assert totals.records_failed == 1
    assert totals.records_failed_open == 1
    assert totals.pass_rate == round(100 * 2 / 3, 2)
    assert totals.items_evaluated == 3
    assert totals.items_failed == 1
    assert totals.items_failed_open == 1
    assert totals.item_pass_rate == totals.pass_rate


def test_multi_item_rule_counts_records_and_items_separately():
    # Two projects, two milestones each; one milestone fails on p1.
    rows = [
        EvaluatedRow("p1", "Milestone A", "s1", True),
        EvaluatedRow("p1", "Milestone B", "s1", False),
        EvaluatedRow("p2", "Milestone A", "s2", True),
        EvaluatedRow("p2", "Milestone B", "s2", True),
    ]

    totals = rule_totals(rows)

    assert totals.records_evaluated == 2
    assert totals.records_failed == 1
    assert totals.records_failed_open == 1
    assert totals.items_evaluated == 4
    assert totals.items_failed == 1
    assert totals.items_failed_open == 1


def test_active_disposition_excludes_from_failed_open_but_not_raw_failed():
    rows = [
        EvaluatedRow("r1", None, "s1", False, has_active_disposition=True),
        EvaluatedRow("r2", None, "s1", False, has_active_disposition=False),
    ]

    totals = rule_totals(rows)

    assert totals.records_failed == 2
    assert totals.records_failed_open == 1
    assert totals.pass_rate == round(100 * 1 / 2, 2)


def test_empty_rows_give_none_pass_rate():
    totals = rule_totals([])

    assert totals.records_evaluated == 0
    assert totals.pass_rate is None
    assert totals.item_pass_rate is None


# --- object_rollup ----------------------------------------------------------------


def test_object_rollup_unions_records_across_rules():
    rule_a = [EvaluatedRow("r1", None, "s1", True), EvaluatedRow("r2", None, "s1", False)]
    rule_b = [EvaluatedRow("r2", None, "s1", True), EvaluatedRow("r3", None, "s2", False)]

    result = object_rollup(
        target_object="SITE",
        record_label="sites",
        object_total=1546,
        active_rules=2,
        rules_succeeded=2,
        rows_by_rule=[rule_a, rule_b],
    )

    assert result.records_evaluated == 3  # r1, r2, r3
    assert result.records_with_exception == 2  # r2 (rule_a), r3 (rule_b)
    assert result.open_findings == 2
    assert result.exception_rate == round(100 * 2 / 3, 2)
    assert result.object_total == 1546


def test_object_rollup_disposition_excludes_from_exceptions():
    rule_a = [EvaluatedRow("r1", None, "s1", False, has_active_disposition=True)]

    result = object_rollup(
        target_object="SITE",
        record_label="sites",
        object_total=None,
        active_rules=1,
        rules_succeeded=1,
        rows_by_rule=[rule_a],
    )

    assert result.records_with_exception == 0
    assert result.open_findings == 0
    assert result.exception_rate == 0.0


def test_object_rollup_no_rows_gives_none_exception_rate():
    result = object_rollup(
        target_object="SITE",
        record_label="sites",
        object_total=0,
        active_rules=0,
        rules_succeeded=0,
        rows_by_rule=[],
    )

    assert result.records_evaluated == 0
    assert result.exception_rate is None


# --- site_scores ----------------------------------------------------------------


def test_site_scores_basic_weighted_score():
    weights = {"CRITICAL": 5, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
    rules = [
        SiteRuleRows("SITE-001", "HIGH", [EvaluatedRow("r1", None, "s1", True)]),
        SiteRuleRows("SITE-002", "MEDIUM", [EvaluatedRow("r2", None, "s1", False)]),
    ]

    scores = site_scores(weights, rules)

    s1 = scores["s1"]
    assert s1.rules_applicable == 2
    assert s1.rules_failed == 1
    assert s1.open_findings == 1
    # weighted_score = 100 * (1 - weight(failed) / weight(applicable)) = 100 * (1 - 2/5)
    assert s1.weighted_score == round(100 * (1 - 2 / 5), 2)


def test_site_scores_rule_not_applicable_excluded_from_site():
    weights = {"CRITICAL": 5, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
    rules = [
        SiteRuleRows("SITE-001", "HIGH", [EvaluatedRow("r1", None, "s1", True)]),
        SiteRuleRows("SITE-002", "MEDIUM", [EvaluatedRow("r2", None, "s2", False)]),
    ]

    scores = site_scores(weights, rules)

    assert scores["s1"].rules_applicable == 1
    assert scores["s1"].weighted_score == 100.0
    assert scores["s2"].rules_applicable == 1
    assert scores["s2"].rules_failed == 1
    assert scores["s2"].weighted_score == 0.0


def test_site_scores_dispositioned_failure_is_not_a_failed_rule():
    weights = {"CRITICAL": 5, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
    rules = [
        SiteRuleRows(
            "SITE-001", "HIGH", [EvaluatedRow("r1", None, "s1", False, has_active_disposition=True)]
        ),
    ]

    scores = site_scores(weights, rules)

    assert scores["s1"].rules_applicable == 1
    assert scores["s1"].rules_failed == 0
    assert scores["s1"].open_findings == 0
    assert scores["s1"].weighted_score == 100.0


def test_site_scores_critical_open_counts_only_critical_severity():
    weights = {"CRITICAL": 5, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
    rules = [
        SiteRuleRows("SITE-001", "CRITICAL", [EvaluatedRow("r1", None, "s1", False)]),
        SiteRuleRows("SITE-002", "MEDIUM", [EvaluatedRow("r2", None, "s1", False)]),
    ]

    scores = site_scores(weights, rules)

    assert scores["s1"].open_findings == 2
    assert scores["s1"].critical_open == 1


def test_site_not_referenced_by_any_rule_has_no_score():
    weights = {"CRITICAL": 5, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
    rules = [SiteRuleRows("SITE-001", "HIGH", [EvaluatedRow("r1", None, "s1", True)])]

    scores = site_scores(weights, rules)

    assert "s2" not in scores
