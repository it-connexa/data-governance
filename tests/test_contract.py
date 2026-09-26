import pyarrow as pa
import pytest

from dq.contract import validate_result


def make_table(
    *,
    record_id,
    item_key=None,
    record_name=None,
    site_id=None,
    site_name=None,
    is_valid,
    detail=None,
) -> pa.Table:
    n = len(record_id)
    return pa.table(
        {
            "RECORD_ID": pa.array(record_id, type=pa.string()),
            "ITEM_KEY": pa.array(item_key or [None] * n, type=pa.string()),
            "RECORD_NAME": pa.array(record_name or [None] * n, type=pa.string()),
            "SITE_ID": pa.array(site_id or [None] * n, type=pa.string()),
            "SITE_NAME": pa.array(site_name or [None] * n, type=pa.string()),
            "IS_VALID": pa.array(is_valid, type=pa.bool_()),
            "DETAIL": pa.array(detail or [None] * n, type=pa.string()),
        }
    )


# --- happy path ---------------------------------------------------------------


def test_valid_single_item_table_passes():
    table = make_table(
        record_id=["r1", "r2", "r3"],
        site_id=["s1", "s1", "s2"],
        is_valid=[True, False, True],
    )

    result = validate_result(table, scope="SITE")

    assert result.ok is True
    assert result.errors == []
    assert result.records_evaluated == 3
    assert result.records_failed == 1
    assert result.records_failed == result.records_evaluated - 2
    assert result.items_evaluated == 3
    assert result.items_failed == 1
    assert result.item_key_used is False
    assert result.null_record_ids == 0
    assert result.null_is_valid == 0
    assert result.null_site_ids == 0
    assert result.duplicate_record_items == []


def test_valid_multi_item_table_counts_records_and_items_separately():
    # Two milestones (items) each on the same two projects (records); one item fails.
    table = make_table(
        record_id=["p1", "p1", "p2", "p2"],
        item_key=["Milestone A", "Milestone B", "Milestone A", "Milestone B"],
        is_valid=[True, False, True, True],
    )

    result = validate_result(table, scope="OBJECT")

    assert result.ok is True
    assert result.item_key_used is True
    assert result.records_evaluated == 2
    assert result.records_failed == 1  # p1 has one failing item
    assert result.items_evaluated == 4
    assert result.items_failed == 1


def test_site_id_null_not_flagged_for_non_site_scope():
    table = make_table(record_id=["r1"], site_id=[None], is_valid=[True])

    result = validate_result(table, scope="OBJECT")

    assert result.ok is True
    assert result.null_site_ids == 1
    assert result.errors == []


# --- violations -----------------------------------------------------------------


def test_null_record_id_fails():
    table = make_table(record_id=["r1", None], is_valid=[True, True])

    result = validate_result(table, scope="OBJECT")

    assert result.ok is False
    assert result.null_record_ids == 1
    assert any("RECORD_ID" in e for e in result.errors)


def test_null_is_valid_fails():
    table = pa.table(
        {
            "RECORD_ID": pa.array(["r1", "r2"], type=pa.string()),
            "ITEM_KEY": pa.array([None, None], type=pa.string()),
            "RECORD_NAME": pa.array([None, None], type=pa.string()),
            "SITE_ID": pa.array([None, None], type=pa.string()),
            "SITE_NAME": pa.array([None, None], type=pa.string()),
            "IS_VALID": pa.array([True, None], type=pa.bool_()),
            "DETAIL": pa.array([None, None], type=pa.string()),
        }
    )

    result = validate_result(table, scope="OBJECT")

    assert result.ok is False
    assert result.null_is_valid == 1
    assert any("IS_VALID" in e for e in result.errors)


def test_duplicate_record_item_pair_fails():
    table = make_table(
        record_id=["r1", "r1"],
        item_key=["Milestone A", "Milestone A"],
        is_valid=[True, False],
    )

    result = validate_result(table, scope="OBJECT")

    assert result.ok is False
    assert result.duplicate_record_items == [("r1", "Milestone A")]
    assert any("duplicate" in e.lower() for e in result.errors)


def test_null_site_id_fails_for_site_scope():
    table = make_table(record_id=["r1", "r2"], site_id=["s1", None], is_valid=[True, True])

    result = validate_result(table, scope="SITE")

    assert result.ok is False
    assert result.null_site_ids == 1
    assert any("SITE_ID" in e for e in result.errors)


def test_missing_required_column_fails():
    table = pa.table(
        {
            "RECORD_ID": pa.array(["r1"], type=pa.string()),
            "IS_VALID": pa.array([True], type=pa.bool_()),
            # ITEM_KEY, RECORD_NAME, SITE_ID, SITE_NAME, DETAIL all missing
        }
    )

    result = validate_result(table, scope="OBJECT")

    assert result.ok is False
    assert any("SITE_ID" in e and "missing" in e.lower() for e in result.errors)
    assert any("DETAIL" in e and "missing" in e.lower() for e in result.errors)


def test_incompatible_column_type_fails():
    table = pa.table(
        {
            "RECORD_ID": pa.array(["r1"], type=pa.string()),
            "ITEM_KEY": pa.array([None], type=pa.string()),
            "RECORD_NAME": pa.array([None], type=pa.string()),
            "SITE_ID": pa.array([None], type=pa.string()),
            "SITE_NAME": pa.array([None], type=pa.string()),
            "IS_VALID": pa.array(["TRUE"], type=pa.string()),  # should be bool
            "DETAIL": pa.array([None], type=pa.string()),
        }
    )

    result = validate_result(table, scope="OBJECT")

    assert result.ok is False
    assert any("IS_VALID" in e for e in result.errors)


@pytest.mark.parametrize("scope", ["SITE", "OBJECT"])
def test_item_key_used_matches_presence_of_non_null_item_key(scope):
    with_items = make_table(
        record_id=["r1"], item_key=["Milestone A"], site_id=["s1"], is_valid=[True]
    )
    without_items = make_table(record_id=["r1"], site_id=["s1"], is_valid=[True])

    assert validate_result(with_items, scope=scope).item_key_used is True
    assert validate_result(without_items, scope=scope).item_key_used is False
