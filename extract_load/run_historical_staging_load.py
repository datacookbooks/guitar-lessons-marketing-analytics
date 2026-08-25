"""Safely orchestrate one selected historical S3 run into staging."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .config import HistoricalRunSettings, PostgresSettings, S3Settings
from .postgres_loader import PageLoadResult, connect_postgres, load_staging_page
from .s3_reader import (
    S3ObjectRef,
    create_s3_client,
    list_historical_run_objects,
    read_staging_page,
)
from .staging_rows import SOURCE_COLUMNS


class HistoricalStagingLoadError(RuntimeError):
    """Raised before writes when the selected run is not the expected delivery."""


@dataclass(frozen=True)
class TableLoadSummary:
    """Per-table outcome for one historical staging-load attempt."""

    table_name: str
    discovered_pages: int
    loaded_pages: int
    skipped_pages: int
    source_rows: int
    loaded_source_rows: int
    skipped_source_rows: int
    final_cursor: int


@dataclass(frozen=True)
class HistoricalLoadSummary:
    """Complete outcome for one selected historical run."""

    table_summaries: tuple[TableLoadSummary, ...]

    @property
    def discovered_pages(self) -> int:
        return sum(item.discovered_pages for item in self.table_summaries)

    @property
    def loaded_pages(self) -> int:
        return sum(item.loaded_pages for item in self.table_summaries)

    @property
    def skipped_pages(self) -> int:
        return sum(item.skipped_pages for item in self.table_summaries)

    @property
    def source_rows(self) -> int:
        return sum(item.source_rows for item in self.table_summaries)


def _page_counts(object_refs: Sequence[S3ObjectRef]) -> dict[str, int]:
    counts = Counter(ref.table_name for ref in object_refs)
    return {
        table_name: counts[table_name]
        for table_name in SOURCE_COLUMNS
        if counts[table_name]
    }


def _print_inventory(
    *,
    historical_run: HistoricalRunSettings,
    page_counts: Mapping[str, int],
    output: Callable[[str], None],
) -> None:
    output(
        "Selected historical run: "
        f"extract_date={historical_run.extract_date}, run_id={historical_run.run_id}"
    )
    output("S3 inventory:")
    for table_name in SOURCE_COLUMNS:
        output(f"  {table_name}: {page_counts.get(table_name, 0):,} objects")
    output(f"  total: {sum(page_counts.values()):,} objects")


def discover_historical_inventory(
    *,
    s3_client: Any,
    bucket_name: str,
    historical_run: HistoricalRunSettings,
    output: Callable[[str], None] = print,
) -> tuple[S3ObjectRef, ...]:
    """Read and report the selected run inventory without opening PostgreSQL."""

    object_refs = list_historical_run_objects(
        s3_client=s3_client,
        bucket_name=bucket_name,
        historical_run=historical_run,
    )
    _print_inventory(
        historical_run=historical_run,
        page_counts=_page_counts(object_refs),
        output=output,
    )
    return object_refs


def _validate_expected_page_counts(
    *,
    actual: Mapping[str, int],
    expected: Mapping[str, int],
) -> None:
    if dict(actual) == dict(expected):
        return

    differences = []
    for table_name in sorted(set(actual) | set(expected)):
        actual_count = actual.get(table_name, 0)
        expected_count = expected.get(table_name, 0)
        if actual_count != expected_count:
            differences.append(
                f"{table_name}: expected {expected_count}, found {actual_count}"
            )

    raise HistoricalStagingLoadError(
        "Selected S3 inventory did not match the expected page counts; "
        + "; ".join(differences)
        + ". No PostgreSQL connection was opened."
    )


def run_historical_staging_load(
    *,
    s3_client: Any,
    bucket_name: str,
    historical_run: HistoricalRunSettings,
    expected_page_counts: Mapping[str, int],
    connection_factory: Callable[[], Any],
    output: Callable[[str], None] = print,
) -> HistoricalLoadSummary:
    """Validate the inventory, then read and apply every page in order."""

    object_refs = discover_historical_inventory(
        s3_client=s3_client,
        bucket_name=bucket_name,
        historical_run=historical_run,
        output=output,
    )
    actual_page_counts = _page_counts(object_refs)
    _validate_expected_page_counts(
        actual=actual_page_counts,
        expected=expected_page_counts,
    )
    output("Inventory matches the expected page counts.")
    output("Opening PostgreSQL only after inventory validation.")

    results: list[PageLoadResult] = []
    connection = connection_factory()
    try:
        for object_ref in object_refs:
            page = read_staging_page(
                s3_client=s3_client,
                bucket_name=bucket_name,
                object_ref=object_ref,
            )
            result = load_staging_page(connection, page)
            results.append(result)
            output(
                f"  {result.table_name} page {object_ref.page_number:05d}: "
                f"{result.status}, {result.source_row_count:,} source rows, "
                f"cursor {result.cursor_start:,}->{result.cursor_end:,}"
            )
    finally:
        connection.close()

    summaries: list[TableLoadSummary] = []
    for table_name in actual_page_counts:
        table_results = [
            result for result in results if result.table_name == table_name
        ]
        loaded_results = [
            result for result in table_results if result.status == "loaded"
        ]
        skipped_results = [
            result for result in table_results if result.status == "skipped"
        ]
        summaries.append(
            TableLoadSummary(
                table_name=table_name,
                discovered_pages=actual_page_counts[table_name],
                loaded_pages=len(loaded_results),
                skipped_pages=len(skipped_results),
                source_rows=sum(result.source_row_count for result in table_results),
                loaded_source_rows=sum(
                    result.source_row_count for result in loaded_results
                ),
                skipped_source_rows=sum(
                    result.source_row_count for result in skipped_results
                ),
                final_cursor=table_results[-1].cursor_end,
            )
        )

    summary = HistoricalLoadSummary(table_summaries=tuple(summaries))
    output("Historical staging-load summary:")
    for item in summary.table_summaries:
        output(
            f"  {item.table_name}: {item.discovered_pages:,} objects, "
            f"{item.loaded_pages:,} loaded, {item.skipped_pages:,} skipped, "
            f"{item.source_rows:,} source rows, "
            f"final cursor {item.final_cursor:,}"
        )
    output(
        f"  total: {summary.discovered_pages:,} objects, "
        f"{summary.loaded_pages:,} loaded, {summary.skipped_pages:,} skipped, "
        f"{summary.source_rows:,} source rows"
    )
    return summary


def _expected_page_counts(value: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in value.split(","):
        name, separator, raw_count = item.strip().partition("=")
        if not separator or name not in SOURCE_COLUMNS:
            raise argparse.ArgumentTypeError(
                "Expected comma-separated TABLE=COUNT values for supported tables."
            )
        if name in counts:
            raise argparse.ArgumentTypeError(f"Duplicate table in inventory: {name}.")
        try:
            count = int(raw_count)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"Expected an integer page count for {name}."
            ) from exc
        if count < 1:
            raise argparse.ArgumentTypeError(
                f"Expected page count for {name} must be at least 1."
            )
        counts[name] = count

    missing = sorted(set(SOURCE_COLUMNS) - set(counts))
    if missing:
        raise argparse.ArgumentTypeError(
            "Expected page counts were missing tables: " + ", ".join(missing)
        )
    return counts


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or load one explicitly selected historical S3 run."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the validated selected run to PostgreSQL staging.",
    )
    parser.add_argument(
        "--expected-page-counts",
        type=_expected_page_counts,
        metavar="TABLE=COUNT,...",
        help="Required with --apply; exact expected inventory for every source table.",
    )
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()

    if not args.apply and args.expected_page_counts is not None:
        parser.error("--expected-page-counts requires --apply.")
    if args.apply and args.expected_page_counts is None:
        parser.error("--apply requires --expected-page-counts.")

    s3_settings = S3Settings.from_env()
    historical_run = HistoricalRunSettings.from_env()
    s3_client = create_s3_client(s3_settings)

    if not args.apply:
        discover_historical_inventory(
            s3_client=s3_client,
            bucket_name=s3_settings.bucket_name,
            historical_run=historical_run,
        )
        print("Read-only inventory complete; no PostgreSQL connection was opened.")
        return

    postgres_settings = PostgresSettings.from_env()
    run_historical_staging_load(
        s3_client=s3_client,
        bucket_name=s3_settings.bucket_name,
        historical_run=historical_run,
        expected_page_counts=args.expected_page_counts,
        connection_factory=lambda: connect_postgres(postgres_settings),
    )


if __name__ == "__main__":
    main()
