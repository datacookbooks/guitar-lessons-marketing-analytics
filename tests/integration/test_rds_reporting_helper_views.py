"""Opt-in rollback validation for live reporting helper views."""

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

load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_RDS_REPORTING_INTEGRATION") != "1",
    reason=(
        "Set RUN_RDS_REPORTING_INTEGRATION=1 to run rollback-only "
        "reporting helper validation."
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

HELPER_VIEWS = (
    "vw_reporting_cutoff",
    "vw_customer_paid_cohort",
    "vw_customer_monthly_subscription_state",
    "vw_customer_monthly_contribution",
    "vw_customer_clv_month",
    "vw_payment_recovery_episode",
    "vw_campaign_assignment_outcome",
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
        raise AssertionError("Unexpected transaction control in helper-view SQL.")

    return [statement.strip() for statement in script.split(";") if statement.strip()]


def _view_snapshot(env: dict[str, str]) -> dict[str, str | None]:
    snapshot: dict[str, str | None] = {}
    with _connect(env) as connection:
        with connection.cursor() as cursor:
            for view_name in HELPER_VIEWS:
                qualified_name = f"analytics.{view_name}"
                cursor.execute("SELECT to_regclass(%s)::text", (qualified_name,))
                if cursor.fetchone()[0] is None:
                    snapshot[view_name] = None
                    continue
                cursor.execute("SELECT pg_get_viewdef(%s::regclass, true)", (qualified_name,))
                snapshot[view_name] = cursor.fetchone()[0]
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


def test_live_reporting_helpers_validate_and_roll_back() -> None:
    env = _environment()
    before = _view_snapshot(env)
    connection = _connect(env)

    try:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '120s'")

            for statement in _statements_without_transaction_control(
                HELPER_SQL_PATH
            ):
                cursor.execute(statement)

            for view_name in HELPER_VIEWS:
                cursor.execute(
                    "SELECT to_regclass(%s)::text",
                    (f"analytics.{view_name}",),
                )
                assert cursor.fetchone()[0] == f"analytics.{view_name}"

            cursor.execute(
                """
                SELECT COUNT(*), MIN(data_through_date), MAX(data_through_date)
                FROM analytics.vw_reporting_cutoff
                """
            )
            cutoff_count, minimum_cutoff, maximum_cutoff = cursor.fetchone()
            assert cutoff_count == 1
            assert minimum_cutoff == maximum_cutoff
            assert str(minimum_cutoff) == "2026-08-25"

            _assert_no_duplicate_grain(
                cursor,
                qualified_view="analytics.vw_customer_paid_cohort",
                grain_columns="customer_id",
            )
            _assert_no_duplicate_grain(
                cursor,
                qualified_view=(
                    "analytics.vw_customer_monthly_subscription_state"
                ),
                grain_columns="customer_id, month_start",
            )
            _assert_no_duplicate_grain(
                cursor,
                qualified_view="analytics.vw_customer_monthly_contribution",
                grain_columns="customer_id, month_start",
            )
            _assert_no_duplicate_grain(
                cursor,
                qualified_view="analytics.vw_customer_clv_month",
                grain_columns="customer_id, month_number",
            )
            _assert_no_duplicate_grain(
                cursor,
                qualified_view="analytics.vw_payment_recovery_episode",
                grain_columns="payment_episode_id",
            )
            _assert_no_duplicate_grain(
                cursor,
                qualified_view="analytics.vw_campaign_assignment_outcome",
                grain_columns="assignment_id",
            )

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM analytics.vw_customer_monthly_subscription_state
                WHERE churned_opening_customer
                  AND NOT is_opening_paid_customer
                """
            )
            assert cursor.fetchone()[0] == 0

            cursor.execute(
                """
                WITH monthly AS (
                    SELECT
                        month_start,
                        SUM(is_opening_paid_customer::integer)
                            AS opening_paid_customers,
                        SUM(churned_opening_customer::integer)
                            AS churned_paid_customers
                    FROM analytics.vw_customer_monthly_subscription_state
                    WHERE is_complete_month
                    GROUP BY month_start
                )
                SELECT
                    MIN(churned_paid_customers::numeric
                        / NULLIF(opening_paid_customers, 0)),
                    MAX(churned_paid_customers::numeric
                        / NULLIF(opening_paid_customers, 0))
                FROM monthly
                WHERE opening_paid_customers > 0
                """
            )
            minimum_churn, maximum_churn = cursor.fetchone()
            assert 0 < minimum_churn < maximum_churn < 1

            cursor.execute(
                """
                SELECT
                    COUNT(*),
                    COUNT(*) FILTER (WHERE recovered_within_7_days)
                FROM analytics.vw_payment_recovery_episode
                """
            )
            failed_episodes, recovered_episodes = cursor.fetchone()
            assert 0 < recovered_episodes <= failed_episodes

            cursor.execute(
                """
                SELECT
                    SUM(realized_contribution),
                    SUM(imputed_amount_events)
                FROM analytics.vw_customer_monthly_contribution
                """
            )
            realized_contribution, imputed_amount_events = cursor.fetchone()
            assert realized_contribution is not None
            assert imputed_amount_events >= 0

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM analytics.fact_campaign_assignment
                WHERE campaign_id = -1
                """
            )
            expected_selected_unknown_rows = cursor.fetchone()[0]
            assert expected_selected_unknown_rows > 0

            cursor.execute("SELECT COUNT(*) FROM analytics.fact_campaign_assignment")
            expected_assignment_rows = cursor.fetchone()[0]

            cursor.execute(
                """
                SELECT
                    COUNT(*),
                    COUNT(DISTINCT assignment_id),
                    COUNT(*) FILTER (WHERE campaign_id = -1),
                    COUNT(*) FILTER (
                        WHERE measurement_window_id = '2026_Q3'
                          AND is_measurement_window_final_30d
                    )
                FROM analytics.vw_campaign_assignment_outcome
                """
            )
            assignment_rows, assignment_ids, unknown_rows, q3_final_rows = (
                cursor.fetchone()
            )
            assert assignment_rows == expected_assignment_rows
            assert assignment_ids == assignment_rows
            assert unknown_rows == expected_selected_unknown_rows
            assert 0 <= q3_final_rows <= assignment_rows

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM analytics.vw_campaign_assignment_outcome
                WHERE is_value_outcome_mature
                  AND campaign_id <> -1
                  AND measurement_window_id = '2024_Q1'
                  AND (
                        effective_net_revenue_90d IS NULL
                        OR prorated_service_cost_90d IS NULL
                        OR realized_contribution_90d IS NULL
                      )
                """
            )
            assert cursor.fetchone()[0] == 0

            cursor.execute(
                """
                SELECT
                    assignment_arm,
                    COUNT(*) FILTER (WHERE is_conversion_outcome_mature)
                        AS mature_assignments,
                    COUNT(*) FILTER (
                        WHERE is_conversion_outcome_mature
                          AND converted_within_30_days
                    ) AS conversions
                FROM analytics.vw_campaign_assignment_outcome
                WHERE campaign_id <> -1
                GROUP BY assignment_arm
                ORDER BY assignment_arm
                """
            )
            experiment_components = cursor.fetchall()
            assert {row[0] for row in experiment_components} == {
                "holdout",
                "treatment",
            }
            assert all(0 < row[2] < row[1] for row in experiment_components)

            print(
                "Reporting helper reconciliation: "
                f"cutoff={minimum_cutoff}; "
                f"complete-month churn range={minimum_churn:.4f}-"
                f"{maximum_churn:.4f}; "
                f"failed episodes={failed_episodes}; "
                f"recovered episodes={recovered_episodes}; "
                f"realized contribution={realized_contribution:.2f}; "
                f"assignment rows={assignment_rows}."
            )
    finally:
        connection.rollback()
        connection.close()

    after = _view_snapshot(env)
    assert after == before
