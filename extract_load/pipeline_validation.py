"""Durable source-to-target validation for recurring ELT runs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from psycopg import sql

from .staging_rows import SOURCE_COLUMNS

ANALYTICS_SOURCE_TABLES = {
    "dim_plan": "dim_plan",
    "dim_campaign": "dim_campaign",
    "dim_customer": "dim_customer",
    "fact_subscription_period": "fact_subscription_period",
    "fact_payment": "fact_payment",
    "fact_campaign_daily": "fact_campaign_daily",
    "fact_campaign_assignment": "fact_campaign_assignment",
}


class PipelineValidationError(RuntimeError):
    """Raised when a recurring-run invariant does not hold."""


@dataclass(frozen=True)
class StagingTableState:
    row_count: int
    distinct_raw_row_ids: int
    rows_without_manifest: int
    watermark: int
    maximum_loaded_cursor: int
    manifest_objects: int
    manifest_source_rows: int


def staging_state(cursor: Any) -> dict[str, StagingTableState]:
    """Return staging, manifest, and watermark state for every source table."""

    cursor.execute(
        """
        SELECT
            watermark.table_name,
            watermark.last_cursor,
            COALESCE(MAX(manifest.source_cursor_end), 0),
            COUNT(manifest.source_s3_key),
            COALESCE(SUM(manifest.source_row_count), 0)
        FROM staging.etl_watermark AS watermark
        LEFT JOIN staging.etl_loaded_object AS manifest
          ON manifest.table_name = watermark.table_name
        GROUP BY watermark.table_name, watermark.last_cursor
        ORDER BY watermark.table_name
        """
    )
    controls = {
        table_name: (
            int(watermark),
            int(maximum_loaded_cursor),
            int(manifest_objects),
            int(manifest_source_rows),
        )
        for (
            table_name,
            watermark,
            maximum_loaded_cursor,
            manifest_objects,
            manifest_source_rows,
        ) in cursor.fetchall()
    }

    result: dict[str, StagingTableState] = {}
    for table_name in SOURCE_COLUMNS:
        cursor.execute(
            sql.SQL(
                """
                SELECT
                    COUNT(*),
                    COUNT(DISTINCT source._raw_row_id),
                    COUNT(*) FILTER (
                        WHERE manifest.source_s3_key IS NULL
                    )
                FROM staging.{} AS source
                LEFT JOIN staging.etl_loaded_object AS manifest
                  ON manifest.source_s3_key = source.source_s3_key
                 AND manifest.table_name = %s
                """
            ).format(sql.Identifier(table_name)),
            (table_name,),
        )
        row_count, distinct_raw_row_ids, rows_without_manifest = cursor.fetchone()
        if table_name not in controls:
            raise PipelineValidationError(
                f"No watermark state exists for {table_name}."
            )
        (
            watermark,
            maximum_loaded_cursor,
            manifest_objects,
            manifest_source_rows,
        ) = controls[table_name]
        result[table_name] = StagingTableState(
            row_count=int(row_count),
            distinct_raw_row_ids=int(distinct_raw_row_ids),
            rows_without_manifest=int(rows_without_manifest),
            watermark=watermark,
            maximum_loaded_cursor=maximum_loaded_cursor,
            manifest_objects=manifest_objects,
            manifest_source_rows=manifest_source_rows,
        )
    return result


def validate_staging_state(state: Mapping[str, StagingTableState]) -> None:
    """Require staging and its control tables to remain internally consistent."""

    expected = set(SOURCE_COLUMNS)
    actual = set(state)
    if actual != expected:
        raise PipelineValidationError(
            "Staging table set changed; "
            f"missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}."
        )

    failures: list[str] = []
    for table_name in SOURCE_COLUMNS:
        item = state[table_name]
        if item.row_count < 1:
            failures.append(f"{table_name} is empty")
        if item.row_count != item.distinct_raw_row_ids:
            failures.append(f"{table_name} contains duplicate raw row IDs")
        if item.rows_without_manifest:
            failures.append(
                f"{table_name} has {item.rows_without_manifest} rows without manifests"
            )
        if item.manifest_objects < 1:
            failures.append(f"{table_name} has no loaded-object manifests")
        if item.manifest_source_rows < item.row_count:
            failures.append(
                f"{table_name} manifests account for fewer rows than staging"
            )
        if item.maximum_loaded_cursor != item.watermark:
            failures.append(
                f"{table_name} watermark {item.watermark} does not match "
                f"loaded cursor {item.maximum_loaded_cursor}"
            )

    if failures:
        raise PipelineValidationError(
            "Staging invariants failed: " + "; ".join(failures) + "."
        )


def analytics_lineage_violations(cursor: Any) -> dict[str, int]:
    """Count analytics rows whose recorded staging delivery is missing."""

    violations: dict[str, int] = {}
    for analytics_table, staging_table in ANALYTICS_SOURCE_TABLES.items():
        synthetic_filter = (
            sql.SQL("AND target.campaign_id <> -1")
            if analytics_table == "dim_campaign"
            else sql.SQL("")
        )
        cursor.execute(
            sql.SQL(
                """
                SELECT COUNT(*)
                FROM analytics.{} AS target
                LEFT JOIN staging.{} AS source
                  ON source._raw_row_id = target.selected_raw_row_id
                 AND source.source_s3_key = target.source_s3_key
                 AND source.source_run_id = target.source_run_id
                WHERE source._raw_row_id IS NULL
                {}
                """
            ).format(
                sql.Identifier(analytics_table),
                sql.Identifier(staging_table),
                synthetic_filter,
            )
        )
        violations[analytics_table] = int(cursor.fetchone()[0])

    for source_table in SOURCE_COLUMNS:
        cursor.execute(
            sql.SQL(
                """
                SELECT COUNT(*)
                FROM analytics.data_quality_issue AS issue
                LEFT JOIN staging.{} AS source
                  ON source._raw_row_id = issue.raw_row_id
                 AND source.source_s3_key = issue.source_s3_key
                 AND source.source_run_id = issue.source_run_id
                WHERE issue.source_table = %s
                  AND source._raw_row_id IS NULL
                """
            ).format(sql.Identifier(source_table)),
            (source_table,),
        )
        violations[f"data_quality_issue:{source_table}"] = int(
            cursor.fetchone()[0]
        )
    return violations


def unknown_campaign_is_valid(cursor: Any) -> bool:
    """Return whether the single synthetic campaign member matches its contract."""

    cursor.execute(
        """
        SELECT COUNT(*) = 1
        FROM analytics.dim_campaign
        WHERE campaign_id = -1
          AND campaign_name = 'Unknown Campaign'
          AND channel = 'unknown'
          AND objective = 'unknown'
          AND primary_conversion_event = 'unknown'
          AND selected_raw_row_id = '__unknown__'
          AND source_s3_key = '__synthetic__'
          AND source_run_id = '__synthetic__'
        """
    )
    return bool(cursor.fetchone()[0])


def validate_analytics_invariants(
    *,
    before_counts: Mapping[str, int],
    after_counts: Mapping[str, int],
    lineage_violations: Mapping[str, int],
    unknown_campaign_valid: bool,
) -> None:
    """Validate durable analytics properties without snapshot-specific totals."""

    if set(before_counts) != set(after_counts):
        raise PipelineValidationError("Analytics table set changed during the run.")

    failures: list[str] = []
    for table_name, after_count in after_counts.items():
        before_count = before_counts[table_name]
        if after_count < before_count:
            failures.append(
                f"{table_name} decreased from {before_count} to {after_count}"
            )
        if table_name != "data_quality_issue" and after_count < 1:
            failures.append(f"{table_name} is empty")

    for name, count in lineage_violations.items():
        if count:
            failures.append(f"{name} has {count} rows with missing staging lineage")

    if not unknown_campaign_valid:
        failures.append("the synthetic unknown campaign does not match its contract")

    if failures:
        raise PipelineValidationError(
            "Analytics invariants failed: " + "; ".join(failures) + "."
        )


def validate_reporting_invariants(
    *,
    analytics_counts: Mapping[str, int],
    reporting_values: Mapping[str, int],
) -> None:
    """Validate relationships that remain true as recurring data grows."""

    required = {
        "new_paid_customers",
        "movement_opening_paid",
        "movement_churned_paid",
        "retention_eligible",
        "retention_retained",
        "failed_billing_episodes",
        "recovered_episodes",
        "campaign_daily_rows",
        "incremental_windows",
        "final_90d_windows",
        "quality_issues",
        "sufficient_plan_clv_rows",
    }
    if set(reporting_values) != required:
        raise PipelineValidationError("Reporting reconciliation component set changed.")

    failures: list[str] = []
    if any(value is None or value < 0 for value in reporting_values.values()):
        raise PipelineValidationError(
            "Reporting invariants failed: a reporting component is null or negative."
        )
    if reporting_values["new_paid_customers"] > analytics_counts["dim_customer"]:
        failures.append("new paid customers exceed modeled customers")
    if (
        reporting_values["movement_churned_paid"]
        > reporting_values["movement_opening_paid"]
    ):
        failures.append("churned paid exposures exceed opening paid exposures")
    if reporting_values["retention_retained"] > reporting_values["retention_eligible"]:
        failures.append("retained customers exceed retention-eligible customers")
    if (
        reporting_values["recovered_episodes"]
        > reporting_values["failed_billing_episodes"]
    ):
        failures.append("recovered episodes exceed failed billing episodes")
    if (
        reporting_values["campaign_daily_rows"]
        > analytics_counts["fact_campaign_daily"]
    ):
        failures.append("reported campaign-day rows exceed modeled campaign-day rows")
    if (
        reporting_values["final_90d_windows"]
        > reporting_values["incremental_windows"]
    ):
        failures.append("final 90-day windows exceed all incremental windows")
    if reporting_values["quality_issues"] != analytics_counts["data_quality_issue"]:
        failures.append("reported quality issues do not reconcile to analytics")
    if reporting_values["sufficient_plan_clv_rows"] > analytics_counts["dim_plan"]:
        failures.append("sufficient plan CLV rows exceed modeled plans")

    if failures:
        raise PipelineValidationError(
            "Reporting invariants failed: " + "; ".join(failures) + "."
        )
