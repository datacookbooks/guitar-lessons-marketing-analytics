"""Orchestrate one canonical recurring API-to-reporting ELT run."""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .config import PostgresSettings, S3Settings, Settings
from .extract import get_table_names
from .pipeline_validation import staging_state, validate_staging_state
from .postgres_loader import connect_postgres
from .run_analytics_transform import (
    analytics_counts,
    apply_analytics_transform,
    connect_analytics_postgres,
)
from .run_incremental_load import (
    IncrementalExtractionSummary,
    IncrementalLoadSummary,
    discover_and_validate_incremental_run,
    extract_incremental_run,
    load_validated_incremental_pages,
    verify_exact_replay,
)
from .run_reporting_views import (
    apply_reporting_views,
    connect_reporting_postgres,
    source_data_through_date,
)
from .s3_reader import create_s3_client
from .s3_writer import S3JsonWriter
from .staging_rows import SOURCE_COLUMNS
from .watermarks import read_watermarks

DAILY_PIPELINE_LOCK_ID = 2_026_092_407_17


class DailyPipelineError(RuntimeError):
    """Raised when the daily pipeline cannot proceed safely."""


@dataclass(frozen=True)
class DailyPipelineSummary:
    source_rows: int
    archived_pages: int
    loaded_pages: int
    data_through_date: str
    analytics_counts: Mapping[str, int]


def validate_source_tables(table_names: list[str]) -> None:
    """Require the API to advertise the complete source contract once."""

    expected = set(SOURCE_COLUMNS)
    actual = set(table_names)
    if len(table_names) != len(actual) or actual != expected:
        raise DailyPipelineError(
            "The API table set did not match the staging contract; "
            f"missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}."
        )


def acquire_pipeline_lock(connection: Any) -> None:
    """Hold one PostgreSQL session-level lock across all pipeline stages."""

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", (DAILY_PIPELINE_LOCK_ID,))
        if not cursor.fetchone()[0]:
            raise DailyPipelineError("Another daily pipeline run already holds the lock.")


def release_pipeline_lock(connection: Any) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(%s)", (DAILY_PIPELINE_LOCK_ID,))


def validate_final_watermarks(
    *,
    actual: Mapping[str, int],
    expected: Mapping[str, int],
) -> None:
    if dict(actual) != dict(expected):
        raise DailyPipelineError(
            "Committed watermarks did not match the validated extraction summary."
        )


def _write_github_summary(summary: DailyPipelineSummary) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not summary_path:
        return

    analytics_rows = sum(
        count
        for table_name, count in summary.analytics_counts.items()
        if table_name != "data_quality_issue"
    )
    quality_issues = summary.analytics_counts["data_quality_issue"]
    content = (
        "## Daily analytics ELT\n\n"
        "| Check | Result |\n"
        "| --- | ---: |\n"
        f"| Source rows received | {summary.source_rows:,} |\n"
        f"| S3 pages archived | {summary.archived_pages:,} |\n"
        f"| S3 pages loaded | {summary.loaded_pages:,} |\n"
        f"| Analytics dimension/fact rows | {analytics_rows:,} |\n"
        f"| Recorded quality issues | {quality_issues:,} |\n"
        f"| Reporting data through | {summary.data_through_date} |\n"
        "\nAll staging, lineage, reconciliation, and rerun checks passed.\n"
    )
    with Path(summary_path).open("a", encoding="utf-8") as summary_file:
        summary_file.write(content)


def _read_and_validate_staging(connection: Any) -> None:
    with connection.cursor() as cursor:
        validate_staging_state(staging_state(cursor))


def inspect_daily_pipeline(
    *,
    api_settings: Settings,
    postgres_settings: PostgresSettings,
    output: Callable[[str], None] = print,
) -> None:
    """Perform read-only API, watermark, and staging readiness checks."""

    with requests.Session() as session:
        table_names = get_table_names(
            api_settings.api_base_url,
            session=session,
            timeout=api_settings.request_timeout_seconds,
        )
    validate_source_tables(table_names)

    connection = connect_postgres(postgres_settings)
    try:
        watermarks = read_watermarks(connection)
        _read_and_validate_staging(connection)
    finally:
        connection.close()

    output("Daily pipeline readiness checks passed.")
    output(f"  API source tables: {len(table_names)}")
    output(f"  PostgreSQL watermarks: {len(watermarks)}")
    output("  no S3 objects or PostgreSQL rows were changed")


def apply_daily_pipeline(
    *,
    api_settings: Settings,
    s3_settings: S3Settings,
    postgres_settings: PostgresSettings,
    output: Callable[[str], None] = print,
) -> DailyPipelineSummary:
    """Run extraction, staging, analytics, reporting, and all validations."""

    control_connection = connect_postgres(postgres_settings)
    lock_acquired = False
    try:
        acquire_pipeline_lock(control_connection)
        lock_acquired = True
        output("Exclusive daily pipeline lock acquired.")

        starting_cursors = read_watermarks(control_connection)
        s3_client = create_s3_client(s3_settings)
        writer = S3JsonWriter(
            bucket_name=s3_settings.bucket_name,
            region_name=s3_settings.region,
            profile_name=s3_settings.profile,
            s3_client=s3_client,
        )
        with requests.Session() as session:
            extraction: IncrementalExtractionSummary = extract_incremental_run(
                settings=api_settings,
                session=session,
                writer=writer,
                starting_cursors=starting_cursors,
                output=output,
            )

        pages = discover_and_validate_incremental_run(
            s3_client=s3_client,
            bucket_name=s3_settings.bucket_name,
            extract_date=extraction.extract_date,
            run_id=extraction.run_id,
            expected_page_counts=extraction.page_counts,
            expected_starting_cursors=extraction.starting_cursors,
            expected_ending_cursors=extraction.ending_cursors,
            output=output,
        )

        connection_factory = lambda: connect_postgres(postgres_settings)
        load_summary: IncrementalLoadSummary = load_validated_incremental_pages(
            pages=pages,
            connection_factory=connection_factory,
            starting_cursors=starting_cursors,
            output=output,
        )
        verify_exact_replay(
            pages=pages,
            connection_factory=connection_factory,
            output=output,
        )
        validate_final_watermarks(
            actual=read_watermarks(control_connection),
            expected=extraction.ending_cursors,
        )
        _read_and_validate_staging(control_connection)
        output("Final staging watermarks and recurring invariants passed.")

        apply_analytics_transform(
            connection=connect_analytics_postgres(postgres_settings),
            verify_rerun=True,
            output=output,
        )
        apply_reporting_views(
            connection=connect_reporting_postgres(postgres_settings),
            verify_rerun=True,
            output=output,
        )

        with control_connection.cursor() as cursor:
            final_analytics_counts = analytics_counts(cursor)
            cutoff = source_data_through_date(cursor)
        if cutoff is None:
            raise DailyPipelineError("The completed run did not produce a cutoff date.")

        summary = DailyPipelineSummary(
            source_rows=extraction.source_row_count,
            archived_pages=extraction.page_count,
            loaded_pages=load_summary.loaded_pages,
            data_through_date=cutoff.isoformat(),
            analytics_counts=final_analytics_counts,
        )
        _write_github_summary(summary)
        output("Daily analytics ELT completed successfully.")
        return summary
    finally:
        if lock_acquired:
            release_pipeline_lock(control_connection)
        control_connection.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or run the canonical recurring analytics ELT pipeline."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the validated API delivery to S3 and PostgreSQL.",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    api_settings = Settings.from_env()
    postgres_settings = PostgresSettings.from_env()

    if not args.apply:
        inspect_daily_pipeline(
            api_settings=api_settings,
            postgres_settings=postgres_settings,
        )
        return

    apply_daily_pipeline(
        api_settings=api_settings,
        s3_settings=S3Settings.from_env(),
        postgres_settings=postgres_settings,
    )


if __name__ == "__main__":
    main()
