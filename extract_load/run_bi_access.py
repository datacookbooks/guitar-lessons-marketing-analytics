"""Deliberately apply and verify least-privilege Power BI database access."""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg

from .config import PostgresSettings


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = (
    PROJECT_ROOT
    / "sql"
    / "bi_access"
    / "001_create_bi_reader_role.sql"
)

BI_ROLE = "marketing_analytics_bi_reader"

APPROVED_RELATIONS = (
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
)

DENIED_RELATIONS = (
    "staging.etl_loaded_object",
    "analytics.dim_customer",
    "analytics.fact_payment",
    "analytics.data_quality_issue",
)


class BIAccessError(RuntimeError):
    """Raised when BI-access deployment or verification fails."""


def sql_body(path: Path = SQL_PATH) -> str:
    """Return the script without its outer transaction commands."""

    script = path.read_text(encoding="utf-8")
    script = re.sub(
        r"\A\s*BEGIN\s*;\s*",
        "",
        script,
        flags=re.IGNORECASE,
    )
    script = re.sub(
        r"\s*COMMIT\s*;\s*\Z",
        "",
        script,
        flags=re.IGNORECASE,
    )

    if re.search(
        r"^\s*(?:BEGIN|COMMIT|ROLLBACK)\s*;",
        script,
        flags=re.IGNORECASE | re.MULTILINE,
    ):
        raise BIAccessError(
            f"Unexpected transaction control remained in {path.name}."
        )

    return script.strip()


def connect_bi_admin_postgres(
    settings: PostgresSettings,
) -> psycopg.Connection:
    """Open a transaction-controlled PostgreSQL connection."""

    connection_kwargs = settings.connection_kwargs()
    connection_kwargs["autocommit"] = False
    return psycopg.connect(**connection_kwargs)


def verify_bi_role(
    cursor: Any,
    *,
    expected_database: str,
) -> None:
    """Verify the role and its explicit allowlist after deployment."""

    cursor.execute("SELECT current_database()")
    actual_database = cursor.fetchone()[0]
    if actual_database != expected_database:
        raise BIAccessError(
            "Database guardrail failed: "
            f"expected {expected_database!r}, found {actual_database!r}."
        )

    cursor.execute(
        """
        SELECT
            rolcanlogin,
            rolsuper,
            rolcreatedb,
            rolcreaterole,
            rolinherit,
            rolreplication,
            rolbypassrls
        FROM pg_catalog.pg_roles
        WHERE rolname = %s
        """,
        (BI_ROLE,),
    )
    attributes = cursor.fetchone()

    if attributes is None:
        raise BIAccessError(f"Expected role is missing: {BI_ROLE}.")

    if attributes != (False, False, False, False, False, False, False):
        raise BIAccessError("The BI role has unexpected role attributes.")

    cursor.execute(
        """
        SELECT
            has_database_privilege(%s, current_database(), 'CONNECT'),
            has_database_privilege(%s, current_database(), 'CREATE')
        """,
        (BI_ROLE, BI_ROLE),
    )
    connect_allowed, create_allowed = cursor.fetchone()

    if not connect_allowed or create_allowed:
        raise BIAccessError("The BI role has unexpected database privileges.")

    for schema_name, expected_usage in (
        ("analytics", True),
        ("reporting", True),
        ("staging", False),
    ):
        cursor.execute(
            """
            SELECT
                has_schema_privilege(%s, %s, 'USAGE'),
                has_schema_privilege(%s, %s, 'CREATE')
            """,
            (BI_ROLE, schema_name, BI_ROLE, schema_name),
        )
        usage_allowed, create_allowed = cursor.fetchone()

        if usage_allowed is not expected_usage or create_allowed:
            raise BIAccessError(
                f"Unexpected BI privileges on schema {schema_name}."
            )

    for relation_name in APPROVED_RELATIONS:
        cursor.execute(
            "SELECT has_table_privilege(%s, %s, 'SELECT')",
            (BI_ROLE, relation_name),
        )
        if cursor.fetchone()[0] is not True:
            raise BIAccessError(
                f"BI SELECT privilege is missing: {relation_name}."
            )

    for relation_name in DENIED_RELATIONS:
        cursor.execute(
            "SELECT has_table_privilege(%s, %s, 'SELECT')",
            (BI_ROLE, relation_name),
        )
        if cursor.fetchone()[0] is not False:
            raise BIAccessError(
                f"Unexpected BI SELECT privilege: {relation_name}."
            )


def apply_bi_access(
    *,
    connection: Any,
    expected_database: str,
    output: Callable[[str], None] = print,
) -> None:
    """Apply, verify, and commit the reusable BI privilege role."""

    try:
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql_body())
                verify_bi_role(
                    cursor,
                    expected_database=expected_database,
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

        output("Power BI privilege role applied and verified.")
        output(f"  role: {BI_ROLE}")
        output(f"  approved relations: {len(APPROVED_RELATIONS)}")
        output("  staging access: denied")
        output("  persistent database writes: denied")
    finally:
        connection.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or deliberately apply Power BI database access."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist the reviewed Power BI privilege role in PostgreSQL.",
    )
    parser.add_argument(
        "--expected-database",
        help="Required with --apply; exact target database name.",
    )
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()

    if not args.apply and args.expected_database is not None:
        parser.error("--expected-database requires --apply.")
    if args.apply and args.expected_database is None:
        parser.error("--apply requires --expected-database.")

    if not args.apply:
        print("Power BI access execution plan:")
        print(f"  SQL: {SQL_PATH.relative_to(PROJECT_ROOT)}")
        print(f"  reusable role: {BI_ROLE}")
        print(f"  approved relations: {len(APPROVED_RELATIONS)}")
        print("  login credentials: created separately outside Git")
        print("Plan inspection complete; no PostgreSQL connection was opened.")
        return

    settings = PostgresSettings.from_env()

    if settings.database != args.expected_database:
        raise BIAccessError(
            "Configured-database guardrail failed: "
            f"expected {args.expected_database!r}, "
            f"configured {settings.database!r}."
        )

    apply_bi_access(
        connection=connect_bi_admin_postgres(settings),
        expected_database=args.expected_database,
    )


if __name__ == "__main__":
    main()
