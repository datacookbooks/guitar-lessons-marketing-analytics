"""Opt-in rollback validation for the live staging-to-analytics SQL."""

from __future__ import annotations

import os
import re
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv
from psycopg import sql


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = PROJECT_ROOT / "sql" / "staging_to_analytics"
DDL_PATH = SQL_DIR / "001_create_analytics_tables.sql"
TRANSFORM_PATH = SQL_DIR / "002_transform_staging_to_analytics.sql"

load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_RDS_ANALYTICS_INTEGRATION") != "1",
    reason=(
        "Set RUN_RDS_ANALYTICS_INTEGRATION=1 to run the rollback-only "
        "analytics validation."
    ),
)

REQUIRED_ENV = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_SSLMODE",
    "POSTGRES_SSLROOTCERT",
    "POSTGRES_CONNECT_TIMEOUT_SECONDS",
)

ANALYTICS_TABLES = (
    "dim_plan",
    "dim_campaign",
    "dim_customer",
    "fact_subscription_period",
    "fact_payment",
    "fact_campaign_daily",
    "fact_campaign_assignment",
    "data_quality_issue",
)

EXPECTED_ANALYTICS_COUNTS = {
    "dim_plan": 3,
    "dim_campaign": 4,
    "dim_customer": 11_273,
    "fact_subscription_period": 14_585,
    "fact_payment": 48_477,
    "fact_campaign_daily": 2_904,
    "fact_campaign_assignment": 89_560,
    "data_quality_issue": 5_848,
}

EXPECTED_QUALITY_COUNTS = {
    "exact_duplicate": 1_875,
    "superseded_delivery": 3_340,
    "missing_value": 62,
    "blank_value": 87,
    "invalid_numeric": 199,
    "unknown_reference": 285,
}


def _environment() -> dict[str, str]:
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        pytest.fail(f"Missing required environment variables: {', '.join(missing)}")
    return {name: os.environ[name] for name in REQUIRED_ENV}


def _connect(env: dict[str, str]) -> psycopg.Connection:
    return psycopg.connect(
        host=env["POSTGRES_HOST"],
        port=int(env["POSTGRES_PORT"]),
        dbname=env["POSTGRES_DB"],
        user=env["POSTGRES_USER"],
        password=env["POSTGRES_PASSWORD"],
        sslmode=env["POSTGRES_SSLMODE"],
        sslrootcert=env["POSTGRES_SSLROOTCERT"],
        connect_timeout=int(env["POSTGRES_CONNECT_TIMEOUT_SECONDS"]),
        autocommit=False,
    )


def _statements_without_transaction_control(path: Path) -> list[str]:
    script = path.read_text(encoding="utf-8")
    script = re.sub(r"\A\s*BEGIN\s*;\s*", "", script, flags=re.IGNORECASE)
    script = re.sub(r"\s*COMMIT\s*;\s*\Z", "", script, flags=re.IGNORECASE)

    if re.search(r"\b(?:BEGIN|COMMIT|ROLLBACK)\s*;", script, re.IGNORECASE):
        raise AssertionError(f"Unexpected transaction control remained in {path}.")

    return [statement.strip() for statement in script.split(";") if statement.strip()]


def _database_snapshot(env: dict[str, str]) -> dict[str, tuple[str | None, int | None]]:
    snapshot: dict[str, tuple[str | None, int | None]] = {}

    with _connect(env) as connection:
        with connection.cursor() as cursor:
            for table_name in ANALYTICS_TABLES:
                qualified_name = f"analytics.{table_name}"
                cursor.execute("SELECT to_regclass(%s)::text", (qualified_name,))
                relation_name = cursor.fetchone()[0]
                row_count = None
                if relation_name is not None:
                    cursor.execute(
                        sql.SQL("SELECT COUNT(*) FROM analytics.{}").format(
                            sql.Identifier(table_name)
                        )
                    )
                    row_count = cursor.fetchone()[0]
                snapshot[table_name] = (relation_name, row_count)

    return snapshot


def _assert_staging_baseline(cursor: psycopg.Cursor) -> None:
    staging_tables = ANALYTICS_TABLES[:7]
    total_rows = 0
    for table_name in staging_tables:
        cursor.execute(
            sql.SQL("SELECT COUNT(*) FROM staging.{}").format(
                sql.Identifier(table_name)
            )
        )
        total_rows += cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM staging.etl_loaded_object")
    manifest_objects = cursor.fetchone()[0]

    assert total_rows == 170_145
    assert manifest_objects == 178


def test_live_analytics_sql_validates_and_rolls_back() -> None:
    env = _environment()
    before = _database_snapshot(env)
    connection = _connect(env)

    try:
        with connection.cursor() as cursor:
            _assert_staging_baseline(cursor)

            for path in (DDL_PATH, TRANSFORM_PATH):
                for statement in _statements_without_transaction_control(path):
                    cursor.execute(statement)

            for table_name, expected_count in EXPECTED_ANALYTICS_COUNTS.items():
                cursor.execute(
                    sql.SQL("SELECT COUNT(*) FROM analytics.{}").format(
                        sql.Identifier(table_name)
                    )
                )
                assert cursor.fetchone()[0] == expected_count

            cursor.execute(
                """
                SELECT issue_code, COUNT(*)
                FROM analytics.data_quality_issue
                GROUP BY issue_code
                """
            )
            quality_counts = dict(cursor.fetchall())
            for issue_code, expected_count in EXPECTED_QUALITY_COUNTS.items():
                assert quality_counts.get(issue_code, 0) == expected_count

            unexpected_nonzero = set(quality_counts) - set(EXPECTED_QUALITY_COUNTS)
            assert not unexpected_nonzero

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM analytics.fact_campaign_assignment
                WHERE campaign_id = -1
                """
            )
            assert cursor.fetchone()[0] > 0
    finally:
        connection.rollback()
        connection.close()

    after = _database_snapshot(env)
    assert after == before
