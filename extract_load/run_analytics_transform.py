"""Deliberately apply and verify the staging-to-analytics SQL layer."""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from .config import PostgresSettings
from .pipeline_validation import (
    analytics_lineage_violations,
    staging_state,
    unknown_campaign_is_valid,
    validate_analytics_invariants,
    validate_staging_state,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = PROJECT_ROOT / "sql" / "staging_to_analytics"
DDL_PATH = SQL_DIR / "001_create_analytics_tables.sql"
TRANSFORM_PATH = SQL_DIR / "002_transform_staging_to_analytics.sql"

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

class AnalyticsTransformError(RuntimeError):
    """Raised when a guardrail or reconciliation check fails."""


def sql_statements(path: Path) -> tuple[str, ...]:
    """Return script statements without its outer transaction commands."""

    script = path.read_text(encoding="utf-8")
    script = re.sub(r"\A\s*BEGIN\s*;\s*", "", script, flags=re.IGNORECASE)
    script = re.sub(r"\s*COMMIT\s*;\s*\Z", "", script, flags=re.IGNORECASE)
    if re.search(r"\b(?:BEGIN|COMMIT|ROLLBACK)\s*;", script, re.IGNORECASE):
        raise AnalyticsTransformError(
            f"Unexpected transaction control remained in {path.name}."
        )
    return tuple(
        statement.strip() for statement in script.split(";") if statement.strip()
    )


def connect_analytics_postgres(settings: PostgresSettings) -> psycopg.Connection:
    """Open a transaction-controlled PostgreSQL connection."""

    connection_kwargs = settings.connection_kwargs()
    connection_kwargs["autocommit"] = False
    return psycopg.connect(**connection_kwargs)


def analytics_counts(cursor: Any) -> dict[str, int]:
    """Return row counts for the seven models and quality support table."""

    counts: dict[str, int] = {}
    for table_name in ANALYTICS_TABLES:
        cursor.execute(
            sql.SQL("SELECT COUNT(*) FROM analytics.{}").format(
                sql.Identifier(table_name)
            )
        )
        counts[table_name] = cursor.fetchone()[0]
    return counts


def quality_counts(cursor: Any) -> dict[str, int]:
    cursor.execute(
        """
        SELECT issue_code, COUNT(*)
        FROM analytics.data_quality_issue
        GROUP BY issue_code
        ORDER BY issue_code
        """
    )
    return dict(cursor.fetchall())


def analytics_fingerprints(cursor: Any) -> dict[str, tuple[int, int]]:
    """Fingerprint complete table values, including audit timestamps."""

    fingerprints: dict[str, tuple[int, int]] = {}
    for table_name in ANALYTICS_TABLES:
        cursor.execute(
            sql.SQL(
                """
                SELECT
                    COUNT(*),
                    COALESCE(
                        SUM(
                            hashtextextended(
                                TO_JSONB(row_value)::text,
                                0
                            )::numeric
                        ),
                        0
                    )
                FROM analytics.{} AS row_value
                """
            ).format(sql.Identifier(table_name))
        )
        row_count, fingerprint = cursor.fetchone()
        fingerprints[table_name] = (row_count, int(fingerprint))
    return fingerprints


def _execute_statements(
    cursor: Any,
    *,
    path: Path,
    output: Callable[[str], None],
) -> None:
    statements = sql_statements(path)
    output(f"Executing {path.name}: {len(statements)} statements.")
    for statement in statements:
        cursor.execute(statement)


def _print_reconciliation(
    *,
    table_counts: Mapping[str, int],
    issue_counts: Mapping[str, int],
    output: Callable[[str], None],
) -> None:
    output("Analytics reconciliation:")
    for table_name in ANALYTICS_TABLES:
        output(f"  {table_name}: {table_counts[table_name]:,} rows")
    output("Quality issues:")
    for issue_code in sorted(issue_counts):
        output(f"  {issue_code}: {issue_counts[issue_code]:,}")


def apply_analytics_transform(
    *,
    connection: Any,
    verify_rerun: bool,
    output: Callable[[str], None] = print,
) -> None:
    """Apply DDL, load analytics, reconcile, and optionally prove invariance."""

    try:
        with connection.cursor() as cursor:
            validate_staging_state(staging_state(cursor))
        connection.rollback()
        output("Staging, manifest, lineage, and watermark guardrails passed.")

        try:
            with connection.cursor() as cursor:
                _execute_statements(cursor, path=DDL_PATH, output=output)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        output("Analytics DDL committed.")

        with connection.cursor() as cursor:
            before_counts = analytics_counts(cursor)
        connection.rollback()

        try:
            with connection.cursor() as cursor:
                _execute_statements(cursor, path=TRANSFORM_PATH, output=output)
                table_counts = analytics_counts(cursor)
                issue_counts = quality_counts(cursor)
                validate_analytics_invariants(
                    before_counts=before_counts,
                    after_counts=table_counts,
                    lineage_violations=analytics_lineage_violations(cursor),
                    unknown_campaign_valid=unknown_campaign_is_valid(cursor),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        output("Initial analytics transformation committed.")
        _print_reconciliation(
            table_counts=table_counts,
            issue_counts=issue_counts,
            output=output,
        )

        if not verify_rerun:
            return

        try:
            with connection.cursor() as cursor:
                before = analytics_fingerprints(cursor)
                _execute_statements(cursor, path=TRANSFORM_PATH, output=output)
                rerun_counts = analytics_counts(cursor)
                validate_analytics_invariants(
                    before_counts=table_counts,
                    after_counts=rerun_counts,
                    lineage_violations=analytics_lineage_violations(cursor),
                    unknown_campaign_valid=unknown_campaign_is_valid(cursor),
                )
                after = analytics_fingerprints(cursor)
                if after != before:
                    raise AnalyticsTransformError(
                        "The unchanged-staging rerun changed analytics values."
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        output("Unchanged-staging rerun was invariant and committed no changes.")
    finally:
        connection.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or deliberately apply the analytics SQL layer."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist the analytics DDL and transformation in RDS.",
    )
    parser.add_argument(
        "--verify-rerun",
        action="store_true",
        help="Rerun unchanged staging and require complete value invariance.",
    )
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()

    if not args.apply and args.verify_rerun:
        parser.error("--verify-rerun requires --apply.")

    if not args.apply:
        print("Analytics SQL execution plan:")
        print(f"  DDL: {DDL_PATH.relative_to(PROJECT_ROOT)}")
        print(f"  transformation: {TRANSFORM_PATH.relative_to(PROJECT_ROOT)}")
        print(f"  modeled tables: {len(ANALYTICS_TABLES)}")
        print("  validation: recurring source-to-target invariants")
        print("Plan inspection complete; no PostgreSQL connection was opened.")
        return

    settings = PostgresSettings.from_env()
    apply_analytics_transform(
        connection=connect_analytics_postgres(settings),
        verify_rerun=args.verify_rerun,
    )


if __name__ == "__main__":
    main()
