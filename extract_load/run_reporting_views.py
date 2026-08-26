"""Deliberately apply and verify the analytics-helper and reporting views."""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import psycopg

from .config import PostgresSettings
from .run_analytics_transform import (
    EXPECTED_ANALYTICS_COUNTS,
    analytics_counts,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
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

VIEW_NAMES = (
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

EXPECTED_RECONCILIATION = {
    "movement_opening_paid": 41_458,
    "movement_churned_paid": 2_664,
    "retention_eligible": 45_261,
    "retention_retained": 29_782,
    "failed_billing_episodes": 4_109,
    "recovered_episodes": 2_969,
    "campaign_daily_rows": 2_904,
    "incremental_windows": 32,
    "final_90d_windows": 26,
    "quality_issues": 5_848,
    "sufficient_plan_clv_rows": 2,
}


class ReportingViewError(RuntimeError):
    """Raised when a reporting-view guardrail or reconciliation fails."""


def sql_statements(path: Path) -> tuple[str, ...]:
    """Return executable statements without comments or outer transaction."""

    script = path.read_text(encoding="utf-8")
    script = re.sub(r"--[^\n]*", "", script)
    script = re.sub(r"\A\s*BEGIN\s*;\s*", "", script, flags=re.IGNORECASE)
    script = re.sub(r"\s*COMMIT\s*;\s*\Z", "", script, flags=re.IGNORECASE)
    if re.search(r"\b(?:BEGIN|COMMIT|ROLLBACK)\s*;", script, re.IGNORECASE):
        raise ReportingViewError(
            f"Unexpected transaction control remained in {path.name}."
        )
    return tuple(
        statement.strip() for statement in script.split(";") if statement.strip()
    )


def connect_reporting_postgres(settings: PostgresSettings) -> psycopg.Connection:
    """Open a transaction-controlled PostgreSQL connection."""

    connection_kwargs = settings.connection_kwargs()
    connection_kwargs["autocommit"] = False
    return psycopg.connect(**connection_kwargs)


def source_data_through_date(cursor: Any) -> date:
    """Return the shared conservative cutoff directly from analytics sources."""

    cursor.execute(
        """
        WITH source_maxima AS (
            SELECT MAX(generated_for_date) AS maximum_date
            FROM analytics.dim_customer
            UNION ALL
            SELECT MAX(generated_for_date)
            FROM analytics.fact_subscription_period
            UNION ALL
            SELECT MAX(generated_for_date)
            FROM analytics.fact_payment
            UNION ALL
            SELECT MAX(generated_for_date)
            FROM analytics.fact_campaign_daily
            UNION ALL
            SELECT MAX(generated_for_date)
            FROM analytics.fact_campaign_assignment
        )
        SELECT MIN(maximum_date)
        FROM source_maxima
        """
    )
    return cursor.fetchone()[0]


def validate_source_guardrails(
    *,
    actual_analytics: Mapping[str, int],
    actual_data_through_date: date,
    expected_data_through_date: date,
) -> None:
    """Stop before writes when the reviewed analytics snapshot changed."""

    if dict(actual_analytics) != EXPECTED_ANALYTICS_COUNTS:
        raise ReportingViewError(
            "Analytics source counts did not match the reviewed expectations."
        )
    if actual_data_through_date != expected_data_through_date:
        raise ReportingViewError(
            "Reporting cutoff guardrail failed: "
            f"expected {expected_data_through_date.isoformat()}, "
            f"found {actual_data_through_date.isoformat()}."
        )


def view_definitions(cursor: Any) -> dict[str, str]:
    """Return normalized PostgreSQL definitions for every reviewed view."""

    definitions: dict[str, str] = {}
    for qualified_name in VIEW_NAMES:
        cursor.execute("SELECT to_regclass(%s)::text", (qualified_name,))
        if cursor.fetchone()[0] != qualified_name:
            raise ReportingViewError(f"Expected view is missing: {qualified_name}.")
        cursor.execute("SELECT pg_get_viewdef(%s::regclass, true)", (qualified_name,))
        definitions[qualified_name] = cursor.fetchone()[0]
    return definitions


def reporting_reconciliation(cursor: Any) -> dict[str, int]:
    """Return exact reviewed reporting components without averaging rates."""

    cursor.execute(
        """
        SELECT
            SUM(opening_paid_customers)::bigint,
            SUM(churned_paid_customers)::bigint
        FROM reporting.vw_monthly_paid_movement
        """
    )
    movement_opening_paid, movement_churned_paid = cursor.fetchone()

    cursor.execute(
        """
        SELECT
            SUM(eligible_customers)::bigint,
            SUM(retained_customers)::bigint
        FROM reporting.vw_paid_cohort_retention
        """
    )
    retention_eligible, retention_retained = cursor.fetchone()

    cursor.execute(
        """
        SELECT
            SUM(failed_billing_episodes)::bigint,
            SUM(recovered_episodes)::bigint
        FROM reporting.vw_payment_recovery
        """
    )
    failed_billing_episodes, recovered_episodes = cursor.fetchone()

    cursor.execute("SELECT COUNT(*) FROM reporting.vw_campaign_daily_performance")
    campaign_daily_rows = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT
            COUNT(*),
            COUNT(*) FILTER (WHERE is_measurement_window_final_90d)
        FROM reporting.vw_campaign_incremental_performance
        """
    )
    incremental_windows, final_90d_windows = cursor.fetchone()

    cursor.execute("SELECT SUM(issue_records) FROM reporting.vw_data_quality")
    quality_issues = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*)
        FROM reporting.vw_expected_12m_paid_clv
        WHERE segment_level = 'plan'
          AND is_sample_sufficient
          AND expected_12m_paid_clv > 0
        """
    )
    sufficient_plan_clv_rows = cursor.fetchone()[0]

    return {
        "movement_opening_paid": movement_opening_paid,
        "movement_churned_paid": movement_churned_paid,
        "retention_eligible": retention_eligible,
        "retention_retained": retention_retained,
        "failed_billing_episodes": failed_billing_episodes,
        "recovered_episodes": recovered_episodes,
        "campaign_daily_rows": campaign_daily_rows,
        "incremental_windows": incremental_windows,
        "final_90d_windows": final_90d_windows,
        "quality_issues": quality_issues,
        "sufficient_plan_clv_rows": sufficient_plan_clv_rows,
    }


def validate_reconciliation(actual: Mapping[str, int]) -> None:
    """Require the live result to match the reviewed profile exactly."""

    if dict(actual) != EXPECTED_RECONCILIATION:
        raise ReportingViewError(
            "Reporting components did not match the reviewed expectations."
        )


def _execute_scripts(cursor: Any, output: Callable[[str], None]) -> None:
    for path in (HELPER_SQL_PATH, REPORTING_SQL_PATH):
        statements = sql_statements(path)
        output(f"Executing {path.name}: {len(statements)} statements.")
        for statement in statements:
            cursor.execute(statement)


def _print_reconciliation(
    values: Mapping[str, int], output: Callable[[str], None]
) -> None:
    output("Reporting reconciliation:")
    for name, value in values.items():
        output(f"  {name}: {value:,}")


def apply_reporting_views(
    *,
    connection: Any,
    expected_data_through_date: date,
    verify_rerun: bool,
    output: Callable[[str], None] = print,
) -> None:
    """Apply, reconcile, commit, and optionally prove replaceability."""

    try:
        with connection.cursor() as cursor:
            actual_analytics = analytics_counts(cursor)
            actual_cutoff = source_data_through_date(cursor)
            validate_source_guardrails(
                actual_analytics=actual_analytics,
                actual_data_through_date=actual_cutoff,
                expected_data_through_date=expected_data_through_date,
            )
        connection.rollback()
        output("Analytics-count and reporting-cutoff guardrails passed.")

        try:
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = '180s'")
                _execute_scripts(cursor, output)
                view_definitions(cursor)
                values = reporting_reconciliation(cursor)
                validate_reconciliation(values)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        output("Reporting helper and dashboard views committed.")
        _print_reconciliation(values, output)

        if not verify_rerun:
            return

        try:
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = '180s'")
                before = view_definitions(cursor)
                _execute_scripts(cursor, output)
                after = view_definitions(cursor)
                if after != before:
                    raise ReportingViewError(
                        "The unchanged-input rerun changed view definitions."
                    )
                rerun_values = reporting_reconciliation(cursor)
                validate_reconciliation(rerun_values)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        output("Unchanged-input rerun was replaceable and reconciled exactly.")
    finally:
        connection.close()


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected a date in YYYY-MM-DD form.") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or deliberately apply the reporting SQL layer."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist the reviewed helper and reporting views in RDS.",
    )
    parser.add_argument(
        "--expected-data-through-date",
        type=_iso_date,
        help="Required with --apply; exact reviewed reporting cutoff.",
    )
    parser.add_argument(
        "--verify-rerun",
        action="store_true",
        help="Reapply unchanged SQL and require definition invariance.",
    )
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()

    if not args.apply and args.expected_data_through_date is not None:
        parser.error("--expected-data-through-date requires --apply.")
    if not args.apply and args.verify_rerun:
        parser.error("--verify-rerun requires --apply.")
    if args.apply and args.expected_data_through_date is None:
        parser.error("--apply requires --expected-data-through-date.")

    if not args.apply:
        print("Reporting SQL execution plan:")
        print(f"  helpers: {HELPER_SQL_PATH.relative_to(PROJECT_ROOT)}")
        print(f"  dashboard views: {REPORTING_SQL_PATH.relative_to(PROJECT_ROOT)}")
        print(f"  reviewed replaceable views: {len(VIEW_NAMES)}")
        print("  reviewed dimension/fact rows: 166,806")
        print("  reviewed quality issues: 5,848")
        print("  reviewed reporting cutoff: 2026-08-25")
        print("Plan inspection complete; no PostgreSQL connection was opened.")
        return

    settings = PostgresSettings.from_env()
    apply_reporting_views(
        connection=connect_reporting_postgres(settings),
        expected_data_through_date=args.expected_data_through_date,
        verify_rerun=args.verify_rerun,
    )


if __name__ == "__main__":
    main()
