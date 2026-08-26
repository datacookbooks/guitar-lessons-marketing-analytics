from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HELPER_SQL_PATH = (
    PROJECT_ROOT
    / "sql"
    / "analytics_views"
    / "001_create_reporting_helper_views.sql"
)
METRIC_DOC_PATH = PROJECT_ROOT / "docs" / "metric_definitions.md"

EXPECTED_HELPER_VIEWS = {
    "vw_reporting_cutoff",
    "vw_customer_paid_cohort",
    "vw_customer_monthly_subscription_state",
    "vw_customer_monthly_contribution",
    "vw_customer_clv_month",
    "vw_payment_recovery_episode",
    "vw_campaign_assignment_outcome",
}


def _helper_sql() -> str:
    return HELPER_SQL_PATH.read_text(encoding="utf-8")


def test_helper_view_script_is_transactional_and_replaceable() -> None:
    sql = _helper_sql()

    assert re.match(r"\s*BEGIN\s*;", sql, re.IGNORECASE)
    assert re.search(r"COMMIT\s*;\s*$", sql, re.IGNORECASE)

    created_views = re.findall(
        r"CREATE\s+OR\s+REPLACE\s+VIEW\s+analytics[.]([a-z0-9_]+)",
        sql,
        re.IGNORECASE,
    )
    assert set(created_views) == EXPECTED_HELPER_VIEWS
    assert len(created_views) == len(EXPECTED_HELPER_VIEWS)


def test_helper_view_script_has_no_destructive_statements() -> None:
    sql_without_comments = re.sub(r"--[^\n]*", "", _helper_sql())

    assert not re.search(
        r"\b(?:DROP|TRUNCATE|DELETE|UPDATE|MERGE)\b",
        sql_without_comments,
        re.IGNORECASE,
    )


def test_churn_helper_restricts_churn_to_opening_paid_customers() -> None:
    sql = _helper_sql()

    assert "is_opening_paid_customer" in sql
    assert "opening.subscription_period_id IS NOT NULL" in sql
    assert "('cancellation', 'move to free')" in sql
    assert "is_complete_month" in sql
    assert "opening_plan_id" in sql


def test_value_helper_aggregates_payment_and_cost_before_joining() -> None:
    sql = _helper_sql()

    assert "payment_month AS" in sql
    assert "service_cost AS" in sql
    assert "FULL OUTER JOIN service_cost" in sql
    assert "is_amount_imputed" in sql
    assert "prorated_service_cost" in sql
    assert "realized_contribution" in sql


def test_clv_helper_uses_exact_anniversaries_and_maturity() -> None:
    sql = _helper_sql()

    assert "first_paid_at" in sql
    assert "checkpoints.month_number * INTERVAL '1 month'" in sql
    assert "is_checkpoint_mature" in sql
    assert "conditional_standard_contribution_margin" in sql


def test_payment_recovery_helper_creates_one_episode_sequence() -> None:
    sql = _helper_sql()

    assert "billing_episode_number" in sql
    assert "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW" in sql
    assert "first_failed_at" in sql
    assert "recovered_within_7_days" in sql


def test_assignment_helper_preserves_itt_and_explicit_windows() -> None:
    sql = _helper_sql()

    assert "event.assignment_arm" in sql
    assert "was_exposed" in sql
    assert "is_conversion_outcome_mature" in sql
    assert "converted_within_30_days" in sql
    assert "is_value_outcome_mature" in sql
    assert "realized_contribution_90d" in sql
    assert "is_measurement_window_final_30d" in sql
    assert "is_measurement_window_final_90d" in sql


def test_metric_document_contains_required_contract_fields() -> None:
    document = METRIC_DOC_PATH.read_text(encoding="utf-8")

    required_phrases = (
        "Business question",
        "Output grain",
        "Eligible population",
        "Date basis",
        "Null and edge treatment",
        "Aggregation class",
        "SQL responsibility",
        "Intended DAX",
        "Semantic-model source / relationships",
    )
    for phrase in required_phrases:
        assert phrase in document

    assert "2026_Q3" in document
    assert "not marked final" in document
