import re
from pathlib import Path


SQL_PATH = (
    Path(__file__).parents[1]
    / "sql"
    / "staging_to_analytics"
    / "001_create_analytics_tables.sql"
)

ANALYTICS_TABLES = (
    "dim_plan",
    "dim_campaign",
    "dim_customer",
    "fact_subscription_period",
    "fact_payment",
    "fact_campaign_daily",
    "fact_campaign_assignment",
)


def _normalized_sql() -> str:
    return re.sub(
        r"\s+",
        " ",
        SQL_PATH.read_text(encoding="utf-8").lower(),
    ).strip()


def _table_body(sql_text: str, table_name: str) -> str:
    match = re.search(
        rf"create table if not exists analytics\.{table_name}\s*"
        rf"\((.*?)\n\);",
        sql_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    assert match is not None, f"Missing analytics.{table_name}"
    return match.group(1).lower()


def test_analytics_ddl_is_versioned_and_rerunnable() -> None:
    assert SQL_PATH.is_file()
    sql = _normalized_sql()

    assert sql.startswith("begin;")
    assert sql.endswith("commit;")
    assert "create table analytics." not in sql
    assert "create index " not in sql.replace("create index if not exists", "")
    assert "on conflict (campaign_id) do nothing" in sql


def test_seven_typed_analytics_tables_and_quality_table_exist() -> None:
    sql_text = SQL_PATH.read_text(encoding="utf-8")

    for table_name in ANALYTICS_TABLES:
        body = _table_body(sql_text, table_name)
        assert "selected_raw_row_id text not null" in body
        assert "source_s3_key text not null" in body
        assert "source_run_id text not null" in body
        assert "transformed_at timestamptz not null" in body

    _table_body(sql_text, "data_quality_issue")


def test_analytics_columns_use_documented_postgresql_types() -> None:
    sql_text = SQL_PATH.read_text(encoding="utf-8")

    expected_fragments = {
        "dim_plan": (
            "plan_id smallint primary key",
            "monthly_price numeric(10, 2)",
            "weekly_recorded_lesson_limit integer",
            "is_paid_plan boolean",
        ),
        "dim_campaign": (
            "campaign_id integer primary key",
            "primary_conversion_event text",
            "active_start_date date",
            "default_treatment_share numeric(5, 4)",
            "is_evergreen boolean",
        ),
        "dim_customer": (
            "customer_id bigint primary key",
            "signup_timestamp timestamptz",
            "source_updated_at timestamptz",
        ),
        "fact_subscription_period": (
            "subscription_period_id text primary key",
            "period_start_timestamp timestamptz",
            "period_end_timestamp timestamptz",
        ),
        "fact_payment": (
            "payment_id text primary key",
            "payment_timestamp timestamptz",
            "amount numeric(10, 2)",
            "attempt_number integer",
        ),
        "fact_campaign_daily": (
            "metric_date date not null",
            "impressions bigint",
            "clicks bigint",
            "spend numeric(12, 2)",
        ),
        "fact_campaign_assignment": (
            "assignment_id text primary key",
            "assigned_at timestamptz",
            "first_exposed_at timestamptz",
        ),
    }

    for table_name, fragments in expected_fragments.items():
        body = _table_body(sql_text, table_name)
        for fragment in fragments:
            assert fragment in body


def test_relationships_unknown_campaign_and_quality_contract_exist() -> None:
    sql = _normalized_sql()
    quality_body = _table_body(
        SQL_PATH.read_text(encoding="utf-8"),
        "data_quality_issue",
    )

    assert "foreign key (first_campaign_id) references analytics.dim_campaign" in sql
    assert "foreign key (customer_id) references analytics.dim_customer" in sql
    assert "foreign key (plan_id) references analytics.dim_plan" in sql
    assert "foreign key (campaign_id) references analytics.dim_campaign" in sql
    assert "values ( -1," in sql
    assert "'unknown campaign'" in sql

    assert "primary key" in quality_body
    for issue_code in (
        "exact_duplicate",
        "superseded_delivery",
        "missing_value",
        "blank_value",
        "invalid_numeric",
        "unknown_reference",
    ):
        assert f"'{issue_code}'" in quality_body


def test_analytics_ddl_has_supporting_indexes_and_no_destructive_sql() -> None:
    sql = _normalized_sql()

    for table_name in ANALYTICS_TABLES:
        assert f"on analytics.{table_name}" in sql

    forbidden = (
        "drop table",
        "drop schema",
        "truncate",
        "delete from",
        "update staging.",
        "insert into staging.",
    )
    for statement in forbidden:
        assert statement not in sql
