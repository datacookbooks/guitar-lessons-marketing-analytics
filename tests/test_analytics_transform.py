import re
from pathlib import Path


SQL_DIR = Path(__file__).parents[1] / "sql" / "staging_to_analytics"
DDL_PATH = SQL_DIR / "001_create_analytics_tables.sql"
TRANSFORM_PATH = SQL_DIR / "002_transform_staging_to_analytics.sql"

ANALYTICS_KEYS = {
    "dim_plan": "plan_id",
    "dim_campaign": "campaign_id",
    "dim_customer": "customer_id",
    "fact_subscription_period": "subscription_period_id",
    "fact_payment": "payment_id",
    "fact_campaign_daily": "metric_date, campaign_id",
    "fact_campaign_assignment": "assignment_id",
}


def _normalized_sql(path: Path) -> str:
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8").lower()).strip()


def test_analytics_sql_files_are_present_and_ordered() -> None:
    files = sorted(path.name for path in SQL_DIR.glob("*.sql"))

    assert DDL_PATH.name in files
    assert TRANSFORM_PATH.name in files
    assert files.index(DDL_PATH.name) < files.index(TRANSFORM_PATH.name)


def test_transformation_uses_one_explicit_transaction() -> None:
    sql = _normalized_sql(TRANSFORM_PATH)

    assert sql.startswith("begin;")
    assert sql.endswith("commit;")
    assert sql.count("begin;") == 1
    assert sql.count("commit;") == 1


def test_all_analytics_models_use_correction_aware_upserts() -> None:
    sql = _normalized_sql(TRANSFORM_PATH)

    for table_name, key_columns in ANALYTICS_KEYS.items():
        assert f"insert into analytics.{table_name}" in sql
        assert f"on conflict ({key_columns}) do update" in sql

    assert sql.count("is distinct from") >= len(ANALYTICS_KEYS)
    assert "transformed_at = current_timestamp" in sql


def test_transform_has_guarded_casts_standardization_and_window_dedup() -> None:
    sql = _normalized_sql(TRANSFORM_PATH)

    assert "case when" in sql
    assert "pg_input_is_valid" in sql
    assert "nullif(btrim(" in sql
    assert "lower(btrim(" in sql
    assert "upper(btrim(" in sql
    assert "row_number() over" in sql
    assert "partition by customer_id" in sql
    assert "partition by subscription_period_id" in sql
    assert "partition by metric_date, source_campaign_id" in sql
    assert "source_updated_at desc nulls last" in sql
    assert "ingested_at desc nulls last" in sql
    assert "raw_delivery_order desc" in sql


def test_campaign_corrections_and_unknown_campaign_policy_are_explicit() -> None:
    sql = _normalized_sql(TRANSFORM_PATH)

    valid_campaign_daily_order = (
        "impressions is not null and clicks is not null "
        "and spend is not null "
        "and platform_attributed_conversions is not null "
        "and ingested_at is not null"
    )
    assert valid_campaign_daily_order in sql
    assert "else -1 end as campaign_id" in sql
    assert "'unknown_reference'" in sql
    assert "from staging.fact_campaign_assignment as source" in sql


def test_campaign_daily_prefers_newer_generation_before_ingestion_time() -> None:
    sql = _normalized_sql(TRANSFORM_PATH)

    campaign_partition = sql.index(
        "partition by metric_date, source_campaign_id"
    )
    completeness_rank = sql.index(
        "and ingested_at is not null ) desc",
        campaign_partition,
    )
    generation_rank = sql.index(
        "generated_for_date desc nulls last",
        campaign_partition,
    )
    ingestion_rank = sql.index(
        "ingested_at desc nulls last",
        campaign_partition,
    )
    extraction_rank = sql.index(
        "extracted_at desc",
        campaign_partition,
    )
    delivery_rank = sql.index(
        "raw_delivery_order desc",
        campaign_partition,
    )

    assert (
        completeness_rank
        < generation_rank
        < ingestion_rank
        < extraction_rank
        < delivery_rank
    )


def test_quality_issues_cover_observed_defects_and_deduplication() -> None:
    sql = _normalized_sql(TRANSFORM_PATH)

    assert sql.count("insert into analytics.data_quality_issue") >= 3
    for issue_code in (
        "missing_value",
        "blank_value",
        "invalid_numeric",
        "unknown_reference",
        "inconsistent_null_pair",
        "exact_duplicate",
        "superseded_delivery",
    ):
        assert f"'{issue_code}'" in sql

    assert "with exact_ranked as" in sql
    assert "with superseded as" in sql
    assert "where exact_rank > 1" in sql


def test_transformation_never_mutates_staging_or_drops_data() -> None:
    sql = _normalized_sql(TRANSFORM_PATH)

    forbidden = (
        "insert into staging.",
        "update staging.",
        "delete from staging.",
        "drop table",
        "drop schema",
        "truncate",
    )
    for statement in forbidden:
        assert statement not in sql
