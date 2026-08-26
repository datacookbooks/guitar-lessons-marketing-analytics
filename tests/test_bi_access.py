from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = (
    PROJECT_ROOT
    / "sql"
    / "bi_access"
    / "001_create_bi_reader_role.sql"
)

BI_ROLE = "marketing_analytics_bi_reader"

APPROVED_SELECT_OBJECTS = {
    "analytics.dim_plan",
    "analytics.dim_campaign",
    "analytics.vw_reporting_cutoff",
    "reporting.vw_monthly_paid_movement",
    "reporting.vw_paid_cohort_retention",
    "reporting.vw_payment_recovery",
    "reporting.vw_customer_value",
    "reporting.vw_paid_clv_monthly_component",
    "reporting.vw_expected_12m_paid_clv",
    "reporting.vw_campaign_daily_performance",
    "reporting.vw_campaign_experiment_arm",
    "reporting.vw_campaign_incremental_performance",
    "reporting.vw_data_quality",
}


def _sql_text() -> str:
    return SQL_PATH.read_text(encoding="utf-8")


def _sql_without_comments() -> str:
    sql = re.sub(
        r"/\*.*?\*/",
        "",
        _sql_text(),
        flags=re.DOTALL,
    )
    return re.sub(r"--[^\n]*", "", sql)


def _normalized_sql() -> str:
    return re.sub(
        r"\s+",
        " ",
        _sql_without_comments().lower(),
    ).strip()


def _granted_select_objects() -> set[str]:
    match = re.search(
        rf"grant\s+select\s+on\s+table\s+(.*?)\s+to\s+{BI_ROLE}\s*;",
        _sql_without_comments(),
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert match is not None, "Missing explicit BI SELECT grant"

    return {
        object_name.strip().lower()
        for object_name in match.group(1).split(",")
    }


def test_bi_access_script_exists_and_is_transactional() -> None:
    assert SQL_PATH.is_file()

    sql = _normalized_sql()

    assert sql.startswith("begin;")
    assert sql.endswith("commit;")


def test_bi_group_role_is_rerunnable_and_cannot_log_in() -> None:
    sql = _normalized_sql()

    assert "from pg_catalog.pg_roles" in sql
    assert f"where rolname = '{BI_ROLE}'" in sql
    assert f"create role {BI_ROLE}" in sql
    assert f"alter role {BI_ROLE}" in sql

    for attribute in (
        "nologin",
        "nosuperuser",
        "nocreatedb",
        "nocreaterole",
        "noinherit",
        "noreplication",
        "nobypassrls",
    ):
        assert attribute in sql


def test_database_privileges_use_current_database_without_secret_values() -> None:
    sql = _normalized_sql()

    assert "current_database()" in sql
    assert (
        f"grant connect on database %i to {BI_ROLE}"
        in sql
    )
    assert (
        f"revoke create on database %i from {BI_ROLE}"
        in sql
    )

    assert "password" not in sql
    assert "create user" not in sql
    assert not re.search(
        rf"create\s+role\s+{BI_ROLE}[^;]*\blogin\b",
        sql,
    )


def test_bi_role_has_no_staging_access() -> None:
    sql = _normalized_sql()

    assert (
        f"revoke all privileges on schema staging from {BI_ROLE}"
        in sql
    )
    assert (
        "revoke all privileges on all tables in schema staging "
        f"from {BI_ROLE}"
        in sql
    )
    assert (
        "revoke all privileges on all sequences in schema staging "
        f"from {BI_ROLE}"
        in sql
    )
    assert (
        "revoke all privileges on all functions in schema staging "
        f"from {BI_ROLE}"
        in sql
    )

    assert not re.search(
        rf"\bgrant\b[^;]*\bstaging\b[^;]*\bto\s+{BI_ROLE}\b",
        sql,
    )


def test_bi_schema_access_is_usage_only() -> None:
    sql = _normalized_sql()

    assert (
        "revoke create on schema analytics, reporting "
        f"from {BI_ROLE}"
        in sql
    )
    assert (
        "grant usage on schema analytics, reporting "
        f"to {BI_ROLE}"
        in sql
    )

    assert (
        f"grant create on schema analytics to {BI_ROLE}"
        not in sql
    )
    assert (
        f"grant create on schema reporting to {BI_ROLE}"
        not in sql
    )


def test_select_grant_matches_exact_approved_allowlist() -> None:
    assert _granted_select_objects() == APPROVED_SELECT_OBJECTS


def test_bi_access_avoids_blanket_and_future_object_grants() -> None:
    sql = _normalized_sql()

    assert "grant select on all tables" not in sql
    assert "alter default privileges" not in sql
    assert "grant all" not in sql
    assert "analytics.dim_customer" not in sql
    assert "analytics.fact_" not in sql
    assert "analytics.data_quality_issue" not in sql


def test_bi_access_contains_no_destructive_or_data_write_statements() -> None:
    assert not re.search(
        r"\b(drop|truncate|insert|update|delete|merge)\b",
        _sql_without_comments(),
        flags=re.IGNORECASE,
    )