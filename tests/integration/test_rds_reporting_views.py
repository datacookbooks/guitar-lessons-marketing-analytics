"""Opt-in rollback validation for live dashboard-facing reporting views."""

from __future__ import annotations

import os
import re
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
HELPER_SQL_PATH = (
    PROJECT_ROOT
    / "sql"
    / "analytics_views"
    / "001_create_reporting_helper_views.sql"
)
REPORTING_SQL_PATH = (
    PROJECT_ROOT
    / "sql"
    / "reporting_views"
    / "001_create_reporting_views.sql"
)

load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_RDS_REPORTING_INTEGRATION") != "1",
    reason=(
        "Set RUN_RDS_REPORTING_INTEGRATION=1 to run rollback-only "
        "dashboard reporting validation."
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

VIEW_REFS = (
    "analytics.vw_reporting_cutoff",
    "analytics.vw_customer_paid_cohort",
    "analytics.vw_customer_monthly_subscription_state",
    "analytics.vw_customer_monthly_contribution",
    "analytics.vw_customer_clv_month",
    "analytics.vw_payment_recovery_episode",
    "analytics.vw_campaign_assignment_outcome",
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
)


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
    script = re.sub(r"--[^\n]*", "", script)
    script = re.sub(r"\A\s*BEGIN\s*;\s*", "", script, flags=re.IGNORECASE)
    script = re.sub(r"\s*COMMIT\s*;\s*\Z", "", script, flags=re.IGNORECASE)
    if re.search(r"\b(?:BEGIN|COMMIT|ROLLBACK)\s*;", script, re.IGNORECASE):
        raise AssertionError(f"Unexpected transaction control in {path.name}.")
    return [statement.strip() for statement in script.split(";") if statement.strip()]


def _view_snapshot(env: dict[str, str]) -> dict[str, str | None]:
    snapshot: dict[str, str | None] = {}
    with _connect(env) as connection:
        with connection.cursor() as cursor:
            for qualified_name in VIEW_REFS:
                cursor.execute("SELECT to_regclass(%s)::text", (qualified_name,))
                if cursor.fetchone()[0] is None:
                    snapshot[qualified_name] = None
                    continue
                cursor.execute("SELECT pg_get_viewdef(%s::regclass, true)", (qualified_name,))
                snapshot[qualified_name] = cursor.fetchone()[0]
    return snapshot


def _assert_no_duplicate_grain(
    cursor: psycopg.Cursor,
    *,
    qualified_view: str,
    grain_columns: str,
) -> None:
    cursor.execute(
        f"""
        SELECT COUNT(*)
        FROM (
            SELECT {grain_columns}
            FROM {qualified_view}
            GROUP BY {grain_columns}
            HAVING COUNT(*) > 1
        ) AS duplicate_grain
        """
    )
    assert cursor.fetchone()[0] == 0


def test_live_reporting_views_validate_and_roll_back() -> None:
    env = _environment()
    before = _view_snapshot(env)
    connection = _connect(env)

    try:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '180s'")

            for path in (HELPER_SQL_PATH, REPORTING_SQL_PATH):
                for statement in _statements_without_transaction_control(path):
                    cursor.execute(statement)

            for qualified_name in VIEW_REFS:
                cursor.execute("SELECT to_regclass(%s)::text", (qualified_name,))
                assert cursor.fetchone()[0] == qualified_name

            _assert_no_duplicate_grain(
                cursor,
                qualified_view="reporting.vw_monthly_paid_movement",
                grain_columns=(
                    "month_start, is_complete_month, opening_plan_id, "
                    "paid_cohort_month, initial_acquisition_channel, "
                    "first_campaign_id, state, experience_level, "
                    "paid_tenure_band"
                ),
            )
            _assert_no_duplicate_grain(
                cursor,
                qualified_view="reporting.vw_paid_cohort_retention",
                grain_columns=(
                    "paid_cohort_month, month_number, initial_paid_plan_id, "
                    "initial_acquisition_channel, first_campaign_id, state, "
                    "experience_level"
                ),
            )
            _assert_no_duplicate_grain(
                cursor,
                qualified_view="reporting.vw_payment_recovery",
                grain_columns=(
                    "failure_month, plan_id, starting_payment_type, "
                    "initial_acquisition_channel, first_campaign_id, state, "
                    "experience_level"
                ),
            )
            _assert_no_duplicate_grain(
                cursor,
                qualified_view="reporting.vw_customer_value",
                grain_columns="customer_id, month_start",
            )
            _assert_no_duplicate_grain(
                cursor,
                qualified_view="reporting.vw_paid_clv_monthly_component",
                grain_columns=(
                    "segment_level, initial_paid_plan_id, "
                    "initial_acquisition_channel, month_number"
                ),
            )
            _assert_no_duplicate_grain(
                cursor,
                qualified_view="reporting.vw_expected_12m_paid_clv",
                grain_columns=(
                    "segment_level, initial_paid_plan_id, "
                    "initial_acquisition_channel"
                ),
            )
            _assert_no_duplicate_grain(
                cursor,
                qualified_view="reporting.vw_campaign_daily_performance",
                grain_columns="campaign_id, metric_date",
            )
            _assert_no_duplicate_grain(
                cursor,
                qualified_view="reporting.vw_campaign_experiment_arm",
                grain_columns=(
                    "campaign_id, measurement_window_id, assignment_arm"
                ),
            )
            _assert_no_duplicate_grain(
                cursor,
                qualified_view=(
                    "reporting.vw_campaign_incremental_performance"
                ),
                grain_columns="campaign_id, measurement_window_id",
            )
            _assert_no_duplicate_grain(
                cursor,
                qualified_view="reporting.vw_data_quality",
                grain_columns=(
                    "source_table, issue_code, is_selected_delivery"
                ),
            )

            cursor.execute(
                """
                SELECT
                    SUM(opening_paid_customers),
                    SUM(churned_paid_customers),
                    SUM(upgrade_events),
                    SUM(downgrade_events),
                    SUM(reactivation_events)
                FROM reporting.vw_monthly_paid_movement
                """
            )
            reporting_movement = cursor.fetchone()
            cursor.execute(
                """
                SELECT
                    SUM(is_opening_paid_customer::integer),
                    SUM(churned_opening_customer::integer),
                    SUM(upgrade_events),
                    SUM(downgrade_events),
                    SUM(reactivation_events)
                FROM analytics.vw_customer_monthly_subscription_state
                """
            )
            assert cursor.fetchone() == reporting_movement

            cursor.execute(
                """
                SELECT
                    SUM(eligible_customers),
                    SUM(retained_customers)
                FROM reporting.vw_paid_cohort_retention
                """
            )
            reporting_retention = cursor.fetchone()
            cursor.execute(
                """
                SELECT
                    COUNT(*),
                    COUNT(*) FILTER (WHERE is_retained_paid)
                FROM analytics.vw_customer_clv_month
                WHERE is_checkpoint_mature
                """
            )
            assert cursor.fetchone() == reporting_retention

            cursor.execute(
                """
                SELECT
                    SUM(failed_billing_episodes),
                    SUM(recovered_episodes)
                FROM reporting.vw_payment_recovery
                """
            )
            reporting_recovery = cursor.fetchone()
            cursor.execute(
                """
                SELECT
                    COUNT(*),
                    COUNT(*) FILTER (WHERE recovered_within_7_days)
                FROM analytics.vw_payment_recovery_episode
                """
            )
            assert cursor.fetchone() == reporting_recovery

            cursor.execute(
                """
                SELECT
                    SUM(effective_net_revenue),
                    SUM(prorated_service_cost),
                    SUM(realized_contribution),
                    SUM(imputed_amount_events)
                FROM reporting.vw_customer_value
                """
            )
            reporting_value = cursor.fetchone()
            cursor.execute(
                """
                SELECT
                    SUM(effective_net_revenue),
                    SUM(prorated_service_cost),
                    SUM(realized_contribution),
                    SUM(imputed_amount_events)
                FROM analytics.vw_customer_monthly_contribution
                """
            )
            assert cursor.fetchone() == reporting_value

            cursor.execute(
                """
                SELECT
                    COUNT(*),
                    SUM(is_clicks_missing::integer),
                    SUM(is_spend_missing::integer),
                    SUM(impressions),
                    SUM(clicks),
                    SUM(spend),
                    SUM(platform_attributed_conversions)
                FROM reporting.vw_campaign_daily_performance
                """
            )
            campaign_daily = cursor.fetchone()
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM analytics.fact_campaign_daily AS daily
                CROSS JOIN analytics.vw_reporting_cutoff AS cutoff
                WHERE daily.metric_date <= cutoff.data_through_date
                """
            )
            assert campaign_daily[0] == cursor.fetchone()[0]
            assert 0 <= campaign_daily[1] <= campaign_daily[0]
            assert 0 <= campaign_daily[2] <= campaign_daily[0]

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM reporting.vw_campaign_experiment_arm
                WHERE campaign_id = -1
                """
            )
            assert cursor.fetchone()[0] == 0

            cursor.execute(
                """
                SELECT
                    SUM(assigned_eligible_people),
                    SUM(mature_conversion_assignments),
                    SUM(conversions_30d),
                    SUM(mature_value_assignments),
                    SUM(effective_net_revenue_90d),
                    SUM(realized_contribution_90d)
                FROM reporting.vw_campaign_experiment_arm
                """
            )
            experiment_arm = cursor.fetchone()
            cursor.execute(
                """
                SELECT
                    COUNT(*),
                    COUNT(*) FILTER (WHERE is_conversion_outcome_mature),
                    COUNT(*) FILTER (
                        WHERE is_conversion_outcome_mature
                          AND converted_within_30_days
                    ),
                    COUNT(*) FILTER (WHERE is_value_outcome_mature),
                    SUM(effective_net_revenue_90d) FILTER (
                        WHERE is_value_outcome_mature
                    ),
                    SUM(realized_contribution_90d) FILTER (
                        WHERE is_value_outcome_mature
                    )
                FROM analytics.vw_campaign_assignment_outcome
                WHERE campaign_id <> -1
                """
            )
            assert cursor.fetchone() == experiment_arm

            cursor.execute(
                """
                SELECT
                    COUNT(*),
                    COUNT(*) FILTER (WHERE is_measurement_window_final_30d),
                    COUNT(*) FILTER (WHERE is_measurement_window_final_90d),
                    COUNT(*) FILTER (
                        WHERE is_measurement_window_final_90d
                          AND missing_spend_days = 0
                          AND incremental_roi_90d IS NOT NULL
                    ),
                    COUNT(*) FILTER (
                        WHERE measurement_window_id = '2026_Q3'
                          AND (
                                is_measurement_window_final_30d
                                OR is_measurement_window_final_90d
                              )
                    )
                FROM reporting.vw_campaign_incremental_performance
                """
            )
            (
                incremental_rows,
                final_30d_rows,
                final_90d_rows,
                populated_roi_rows,
                q3_final_rows,
            ) = cursor.fetchone()
            assert incremental_rows > 0
            assert 0 < final_90d_rows <= final_30d_rows < incremental_rows
            assert 0 < populated_roi_rows <= final_90d_rows
            assert 0 <= q3_final_rows <= incremental_rows

            cursor.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE segment_level = 'plan'),
                    COUNT(*) FILTER (
                        WHERE segment_level = 'plan'
                          AND is_sample_sufficient
                          AND expected_12m_paid_clv > 0
                    ),
                    MIN(expected_12m_paid_clv) FILTER (
                        WHERE is_sample_sufficient
                    ),
                    MAX(expected_12m_paid_clv) FILTER (
                        WHERE is_sample_sufficient
                    )
                FROM reporting.vw_expected_12m_paid_clv
                """
            )
            plan_clv_rows, sufficient_plan_clv_rows, minimum_clv, maximum_clv = (
                cursor.fetchone()
            )
            cursor.execute(
                "SELECT COUNT(*) FROM analytics.dim_plan WHERE is_paid_plan"
            )
            assert plan_clv_rows == cursor.fetchone()[0]
            assert 0 < sufficient_plan_clv_rows <= plan_clv_rows
            assert 0 < minimum_clv <= maximum_clv

            cursor.execute(
                """
                SELECT SUM(issue_records)
                FROM reporting.vw_data_quality
                """
            )
            reporting_quality_issues = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM analytics.data_quality_issue")
            assert cursor.fetchone()[0] == reporting_quality_issues

            complete_opening, complete_churned = reporting_movement[:2]
            weighted_churn = complete_churned / complete_opening
            print(
                "Reporting reconciliation: "
                f"movement opening={complete_opening}, "
                f"churned={complete_churned}, "
                f"pooled churn={weighted_churn:.4f}; "
                f"retention components={reporting_retention}; "
                f"recovery components={reporting_recovery}; "
                f"campaign daily rows={campaign_daily[0]}; "
                f"incremental windows={incremental_rows}, "
                f"final 90-day windows={final_90d_rows}; "
                f"CLV range={minimum_clv:.2f}-{maximum_clv:.2f}; "
                f"quality issues={reporting_quality_issues}."
            )
    finally:
        connection.rollback()
        connection.close()

    after = _view_snapshot(env)
    assert after == before
