from pathlib import Path

import duckdb
import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SCHEMA_SQL = Path(__file__).parent.parent / "sql" / "dq_poc_duckdb_schema.sql"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    """A fresh temp DuckDB file built from the real schema file. Never the
    real ~/dq-data/governance.duckdb."""
    db_path = tmp_path / "governance.duckdb"
    con = duckdb.connect(str(db_path))
    try:
        con.execute(SCHEMA_SQL.read_text())
    finally:
        con.close()
    return db_path
