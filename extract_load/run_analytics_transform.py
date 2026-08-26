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
from .staging_rows import SOURCE_COLUMNS


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


def staging_baseline(cursor: Any) -> tuple[int, int]:
    """Return total staging rows and loaded-object manifests."""

    total_rows = 0
    for table_name in SOURCE_COLUMNS:
        cursor.execute(
            sql.SQL("SELECT COUNT(*) FROM staging.{}").format(
                sql.Identifier(table_name)
            )
        )
        total_rows += cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM staging.etl_loaded_object")
    return total_rows, cursor.fetchone()[0]


def validate_staging_baseline(
    cursor: Any,
    *,
    expected_staging_rows: int,
    expected_manifest_objects: int,
) -> None:
    """Stop before analytics writes when the documented baseline changed."""

    staging_rows, manifest_objects = staging_baseline(cursor)
    if staging_rows != expected_staging_rows:
        raise AnalyticsTransformError(
            "Staging-row guardrail failed: "
            f"expected {expected_staging_rows:,}, found {staging_rows:,}."
        )
    if manifest_objects != expected_manifest_objects:
        raise AnalyticsTransformError(
            "Manifest guardrail failed: "
            f"expected {expected_manifest_objects:,}, "
            f"found {manifest_objects:,}."
        )


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


def validate_reconciliation(
    *,
    actual_analytics: Mapping[str, int],
    actual_quality: Mapping[str, int],
) -> None:
    """Require the live result to match the reviewed profile exactly."""

    if dict(actual_analytics) != EXPECTED_ANALYTICS_COUNTS:
        raise AnalyticsTransformError(
            "Analytics row counts did not match the reviewed expectations."
        )
    if dict(actual_quality) != EXPECTED_QUALITY_COUNTS:
        raise AnalyticsTransformError(
            "Quality-issue counts did not match the reviewed expectations."
        )


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
    expected_staging_rows: int,
    expected_manifest_objects: int,
    verify_rerun: bool,
    output: Callable[[str], None] = print,
) -> None:
    """Apply DDL, load analytics, reconcile, and optionally prove invariance."""

    try:
        with connection.cursor() as cursor:
            validate_staging_baseline(
                cursor,
                expected_staging_rows=expected_staging_rows,
                expected_manifest_objects=expected_manifest_objects,
            )
        connection.rollback()
        output("Staging and manifest guardrails passed.")

        try:
            with connection.cursor() as cursor:
                _execute_statements(cursor, path=DDL_PATH, output=output)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        output("Analytics DDL committed.")

        try:
            with connection.cursor() as cursor:
                _execute_statements(cursor, path=TRANSFORM_PATH, output=output)
                table_counts = analytics_counts(cursor)
                issue_counts = quality_counts(cursor)
                validate_reconciliation(
                    actual_analytics=table_counts,
                    actual_quality=issue_counts,
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
                rerun_quality = quality_counts(cursor)
                validate_reconciliation(
                    actual_analytics=rerun_counts,
                    actual_quality=rerun_quality,
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


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected a positive integer.") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("Expected a positive integer.")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or deliberately apply the analytics SQL layer."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist the reviewed analytics DDL and transformation in RDS.",
    )
    parser.add_argument(
        "--expected-staging-rows",
        type=_positive_int,
        help="Required with --apply; exact reviewed staging-row count.",
    )
    parser.add_argument(
        "--expected-manifest-objects",
        type=_positive_int,
        help="Required with --apply; exact reviewed loaded-object count.",
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

    confirmations = (
        args.expected_staging_rows,
        args.expected_manifest_objects,
    )
    if not args.apply and any(value is not None for value in confirmations):
        parser.error("Expected-count confirmations require --apply.")
    if not args.apply and args.verify_rerun:
        parser.error("--verify-rerun requires --apply.")
    if args.apply and any(value is None for value in confirmations):
        parser.error(
            "--apply requires --expected-staging-rows and "
            "--expected-manifest-objects."
        )

    if not args.apply:
        print("Analytics SQL execution plan:")
        print(f"  DDL: {DDL_PATH.relative_to(PROJECT_ROOT)}")
        print(f"  transformation: {TRANSFORM_PATH.relative_to(PROJECT_ROOT)}")
        print("  reviewed analytics rows: 166,806 including the unknown campaign")
        print("  reviewed quality issues: 5,848")
        print("Plan inspection complete; no PostgreSQL connection was opened.")
        return

    settings = PostgresSettings.from_env()
    apply_analytics_transform(
        connection=connect_analytics_postgres(settings),
        expected_staging_rows=args.expected_staging_rows,
        expected_manifest_objects=args.expected_manifest_objects,
        verify_rerun=args.verify_rerun,
    )


if __name__ == "__main__":
    main()
