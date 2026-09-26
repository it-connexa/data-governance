from datetime import datetime

import pyarrow as pa
import pytest

from dq import catalogue
from dq.engine import ExecutionResult
from dq.sources import load_sources
from dq.store.duckdb_store import DuckDBStore


class FakeCatalogueClient:
    """Maps a table name appearing in the SQL to a canned INFORMATION_SCHEMA
    result -- never a real Snowflake connection."""

    def __init__(self, tables_by_name: dict[str, pa.Table]):
        self._tables = tables_by_name
        self.calls: list[str] = []

    def execute(self, sql, *, query_tag):
        self.calls.append(sql)
        for table_name, table in self._tables.items():
            if table_name in sql:
                return ExecutionResult(table=table, query_id="fake-query-id", duration_ms=1)
        raise AssertionError(f"No fake table configured for SQL: {sql}")


def columns_table(rows: list[tuple]) -> pa.Table:
    names, types, nullables, positions, comments = zip(*rows, strict=True)
    return pa.table(
        {
            "COLUMN_NAME": pa.array(names, type=pa.string()),
            "DATA_TYPE": pa.array(types, type=pa.string()),
            "IS_NULLABLE": pa.array(nullables, type=pa.string()),
            "ORDINAL_POSITION": pa.array(positions, type=pa.int64()),
            "COMMENT": pa.array(comments, type=pa.string()),
        }
    )


@pytest.fixture
def sources_config(fixtures_dir):
    return load_sources(fixtures_dir / "sources_test.yml")


def test_refresh_loads_columns_for_every_source(store_path, sources_config):
    store = DuckDBStore(store_path)
    client = FakeCatalogueClient(
        {
            "SITE__C": columns_table(
                [
                    ("ID", "VARCHAR", "NO", 1, None),
                    ("NAME", "VARCHAR", "YES", 2, "Site name"),
                ]
            ),
            "PROJECT__C": columns_table([("ID", "VARCHAR", "NO", 1, None)]),
            "LEGACY__C": columns_table([("ID", "VARCHAR", "NO", 1, None)]),
        }
    )

    counts = catalogue.refresh(
        store, client, sources_config, query_tag="dq_poc:catalogue_refresh:1"
    )

    assert counts == {"SITE": 2, "PROJECT": 1, "NO_DEFAULT_FILTERS": 1}
    site_columns = store.get_source_columns(source_name="SITE")
    assert [c["COLUMN_NAME"] for c in site_columns] == ["ID", "NAME"]
    assert site_columns[0]["IS_NULLABLE"] is False
    assert site_columns[1]["IS_NULLABLE"] is True
    assert site_columns[1]["COLUMN_COMMENT"] == "Site name"


def test_refresh_uses_correct_database_schema_and_table_in_query(store_path, sources_config):
    store = DuckDBStore(store_path)
    client = FakeCatalogueClient(
        {
            "SITE__C": columns_table([("ID", "VARCHAR", "NO", 1, None)]),
            "PROJECT__C": columns_table([("ID", "VARCHAR", "NO", 1, None)]),
            "LEGACY__C": columns_table([("ID", "VARCHAR", "NO", 1, None)]),
        }
    )

    catalogue.refresh(store, client, sources_config, query_tag="dq_poc:catalogue_refresh:1")

    site_call = next(c for c in client.calls if "SITE__C" in c)
    assert "TESTDB.INFORMATION_SCHEMA.COLUMNS" in site_call
    assert "TABLE_SCHEMA = 'TESTSCHEMA'" in site_call
    assert "TABLE_NAME = 'SITE__C'" in site_call


def test_refresh_raises_catalogue_error_on_empty_result(store_path, sources_config):
    store = DuckDBStore(store_path)
    empty = pa.table(
        {
            "COLUMN_NAME": pa.array([], type=pa.string()),
            "DATA_TYPE": pa.array([], type=pa.string()),
            "IS_NULLABLE": pa.array([], type=pa.string()),
            "ORDINAL_POSITION": pa.array([], type=pa.int64()),
            "COMMENT": pa.array([], type=pa.string()),
        }
    )
    client = FakeCatalogueClient({"SITE__C": empty, "PROJECT__C": empty, "LEGACY__C": empty})

    with pytest.raises(catalogue.CatalogueError, match="SITE"):
        catalogue.refresh(store, client, sources_config, query_tag="dq_poc:catalogue_refresh:1")


def test_load_labels_updates_source_column(store_path, sources_config, tmp_path):
    store = DuckDBStore(store_path)
    store.upsert_source_columns(
        "SITE",
        [
            {
                "column_name": "SITETRACKER_SITE_STATUS_C",
                "data_type": "VARCHAR",
                "ordinal_position": 1,
            }
        ],
        datetime.now(),
    )
    csv_path = tmp_path / "field_labels.csv"
    csv_path.write_text("source,column_name,label\nSITE,SITETRACKER_SITE_STATUS_C,Site Status\n")

    count = catalogue.load_labels(store, csv_path)

    assert count == 1
    columns = store.get_source_columns(source_name="SITE")
    assert columns[0]["COLUMN_LABEL"] == "Site Status"


def test_load_labels_missing_file_raises(store_path):
    store = DuckDBStore(store_path)

    with pytest.raises(catalogue.CatalogueError, match="not found"):
        catalogue.load_labels(store, "/no/such/file.csv")


def test_load_labels_bad_header_raises(store_path, tmp_path):
    store = DuckDBStore(store_path)
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("source,col,lbl\nSITE,X,Y\n")

    with pytest.raises(catalogue.CatalogueError, match="columns"):
        catalogue.load_labels(store, csv_path)


def test_show_delegates_to_store(store_path, sources_config):
    store = DuckDBStore(store_path)
    store.upsert_source_columns(
        "SITE",
        [{"column_name": "ID", "data_type": "VARCHAR", "ordinal_position": 1}],
        datetime.now(),
    )

    rows = catalogue.show(store, source_name="SITE")

    assert [r["COLUMN_NAME"] for r in rows] == ["ID"]
