from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORTING_SQL_PATH = (
    PROJECT_ROOT
    / "sql"
    / "reporting_views"
    / "001_create_reporting_views.sql"
)

EXPECTED_REPORTING_VIEWS = {
    "vw_new_paid_customers_monthly",
    "vw_monthly_paid_movement",
    "vw_paid_cohort_retention",
    "vw_payment_recovery",
    "vw_customer_value",
    "vw_paid_clv_monthly_component",
    "vw_expected_12m_paid_clv",
    "vw_campaign_daily_performance",
    "vw_campaign_experiment_arm",
    "vw_campaign_incremental_performance",
    "vw_data_quality",
}


def _reporting_sql() -> str:
    return REPORTING_SQL_PATH.read_text(encoding="utf-8")


def test_reporting_script_is_transactional_and_replaceable() -> None:
    sql = _reporting_sql()

    assert re.match(r"\s*BEGIN\s*;", sql, re.IGNORECASE)
    assert re.search(r"COMMIT\s*;\s*$", sql, re.IGNORECASE)
    created_views = re.findall(
        r"CREATE\s+OR\s+REPLACE\s+VIEW\s+reporting[.]([a-z0-9_]+)",
        sql,
        re.IGNORECASE,
    )
    assert set(created_views) == EXPECTED_REPORTING_VIEWS
    assert len(created_views) == len(EXPECTED_REPORTING_VIEWS)


def test_reporting_script_has_no_destructive_statements() -> None:
    sql_without_comments = re.sub(r"--[^\n]*", "", _reporting_sql())

    assert not re.search(
        r"\b(?:DROP|TRUNCATE|DELETE|UPDATE|MERGE)\b",
        sql_without_comments,
        re.IGNORECASE,
    )


def test_new_paid_customers_uses_first_paid_cohort_once() -> None:
    sql = _reporting_sql()
    view_start = sql.index(
        "CREATE OR REPLACE VIEW reporting.vw_new_paid_customers_monthly"
    )
    view_end = sql.index("CREATE OR REPLACE VIEW", view_start + 1)
    view_sql = re.sub(r"\s+", " ", sql[view_start:view_end]).lower()

    assert "from analytics.vw_customer_paid_cohort as cohort" in view_sql
    assert "cohort.paid_cohort_month as month_start" in view_sql
    assert "'unknown/unattributed'" in view_sql
    assert "coalesce(cohort.first_campaign_id, -1) as first_campaign_id" in view_sql
    assert "cohort.initial_paid_plan_id" in view_sql
    assert "count(*) as new_paid_customers" in view_sql
    assert " join " not in view_sql


def test_churn_and_retention_views_expose_components_and_safe_rates() -> None:
    sql = _reporting_sql()

    for phrase in (
        "opening_paid_customers",
        "churned_paid_customers",
        "paid_churn_rate",
        "eligible_customers",
        "retained_customers",
        "paid_retention_rate",
        "NULLIF",
    ):
        assert phrase in sql

    assert not re.search(
        r"AVG\s*\([^)]*(?:churn|retention)[^)]*rate",
        sql,
        re.IGNORECASE,
    )


def test_payment_and_value_views_keep_compatible_grains() -> None:
    sql = _reporting_sql()

    assert "vw_payment_recovery_episode" in sql
    assert "failed_billing_episodes" in sql
    assert "recovered_episodes" in sql
    assert "vw_customer_monthly_contribution" in sql
    assert "imputed_amount_events" in sql
    assert "realized_contribution" in sql


def test_clv_views_enforce_sample_size_and_nonadditive_components() -> None:
    sql = _reporting_sql()

    assert "GROUPING SETS" in sql
    assert "expected_monthly_contribution" in sql
    assert "expected_12m_paid_clv" in sql
    assert "MIN(component.eligible_customers) >= 30" in sql
    assert "new_paid_weight" in sql


def test_campaign_daily_rates_use_additive_components() -> None:
    sql = _reporting_sql()

    for phrase in (
        "is_clicks_missing",
        "is_spend_missing",
        "click_through_rate",
        "cost_per_click",
        "platform_cost_per_attributed_conversion",
    ):
        assert phrase in sql

    assert "daily.clicks::numeric / NULLIF(daily.impressions, 0)" in sql
    assert "daily.spend / NULLIF(daily.clicks, 0)" in sql


def test_experiment_views_preserve_arms_maturity_and_itt_components() -> None:
    sql = _reporting_sql()

    for phrase in (
        "assignment_arm",
        "mature_conversion_assignments",
        "mature_value_assignments",
        "is_measurement_window_final_30d",
        "is_measurement_window_final_90d",
        "treatment_eligible_30d",
        "holdout_eligible_30d",
        "incremental_conversion_lift",
    ):
        assert phrase in sql


def test_campaign_spend_is_aggregated_before_assignment_join() -> None:
    sql = _reporting_sql()

    assert "window_spend AS" in sql
    assert "GROUP BY bounds.campaign_id, bounds.measurement_window_id" in sql
    assert "JOIN window_spend AS spend" in sql
    assert "estimated_incremental_revenue_90d" in sql
    assert "estimated_incremental_contribution_90d" in sql
    assert "incremental_roas_90d" in sql
    assert "incremental_roi_90d" in sql


def test_quality_view_exposes_issue_and_impacted_row_counts() -> None:
    sql = _reporting_sql()

    assert "analytics.data_quality_issue" in sql
    assert "issue_records" in sql
    assert "COUNT(DISTINCT issue.raw_row_id) AS impacted_raw_rows" in sql
    assert "issue.is_selected_delivery" in sql
