import re
from pathlib import Path

from extract_load.staging_rows import LINEAGE_COLUMNS, SOURCE_COLUMNS

SCHEMA_DIR = Path(__file__).parents[1] / "sql" / "schema"
EXPECTED_FILES = [
    "001_create_schemas.sql",
    "002_create_staging_tables.sql",
    "003_create_etl_control.sql",
]


def _normalized_sql(path: Path) -> str:
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8").lower())


def _table_body(sql: str, qualified_table_name: str) -> str:
    match = re.search(
        rf"create table {re.escape(qualified_table_name)}\s*\((.*?)\n\);",
        sql,
        flags=re.DOTALL | re.IGNORECASE,
    )
    assert match is not None, f"Missing CREATE TABLE for {qualified_table_name}"
    return match.group(1).lower()


def test_versioned_schema_files_exist() -> None:
    actual_files = sorted(path.name for path in SCHEMA_DIR.glob("*.sql"))
    for expected_file in EXPECTED_FILES:
        assert expected_file in actual_files

    assert actual_files.index(EXPECTED_FILES[0]) < actual_files.index(EXPECTED_FILES[1])
    assert actual_files.index(EXPECTED_FILES[1]) < actual_files.index(EXPECTED_FILES[2])


def test_three_schemas_are_created() -> None:
    sql = _normalized_sql(SCHEMA_DIR / EXPECTED_FILES[0])

    for schema_name in ("staging", "analytics", "reporting"):
        assert f"create schema if not exists {schema_name}" in sql


def test_staging_tables_preserve_source_columns_as_text() -> None:
    sql = (SCHEMA_DIR / EXPECTED_FILES[1]).read_text(encoding="utf-8")

    for table_name, source_columns in SOURCE_COLUMNS.items():
        body = _table_body(sql, f"staging.{table_name}")

        for column_name in source_columns:
            assert re.search(rf"\b{re.escape(column_name)}\s+text\b", body)

        assert "primary key" in body
        assert "foreign key" not in body

        for lineage_column in LINEAGE_COLUMNS:
            assert re.search(rf"\b{lineage_column}\b", body)


def test_etl_control_uses_an_explicit_bigint_cursor() -> None:
    sql = _normalized_sql(SCHEMA_DIR / EXPECTED_FILES[2])

    assert "create table staging.etl_watermark" in sql
    assert "last_cursor bigint not null default 0" in sql
    assert "last_raw_row_id" not in sql
    assert "create table staging.etl_loaded_object" in sql
    assert "source_s3_key text primary key" in sql

    for table_name in SOURCE_COLUMNS:
        assert f"('{table_name}')" in sql


def test_schema_migrations_do_not_drop_existing_objects() -> None:
    all_sql = "\n".join(
        path.read_text(encoding="utf-8").lower() for path in SCHEMA_DIR.glob("*.sql")
    )

    assert "drop table" not in all_sql
    assert "drop schema" not in all_sql
