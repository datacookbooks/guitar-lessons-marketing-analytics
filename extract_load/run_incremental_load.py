"""Manually extract and load one replay-safe incremental delivery."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

from .config import PostgresSettings, S3Settings, Settings
from .extract import get_table_names, iter_table_pages
from .postgres_loader import PageLoadResult, connect_postgres, load_staging_page
from .s3_reader import (
    S3ObjectRef,
    create_s3_client,
    list_incremental_run_objects,
    read_staging_page,
)
from .s3_writer import S3JsonWriter
from .staging_rows import SOURCE_COLUMNS, StagingPage
from .watermarks import read_watermarks


class IncrementalRunError(RuntimeError):
    """Raised when an incremental run cannot proceed without ambiguity."""


@dataclass(frozen=True)
class IncrementalTableSummary:
    """Extraction outcome for one supported source table."""

    table_name: str
    starting_cursor: int
    ending_cursor: int
    page_count: int
    source_row_count: int


@dataclass(frozen=True)
class IncrementalExtractionSummary:
    """The immutable contract produced by one incremental extraction."""

    extracted_at: datetime
    table_summaries: tuple[IncrementalTableSummary, ...]

    @property
    def extract_date(self) -> str:
        return self.extracted_at.strftime("%Y-%m-%d")

    @property
    def run_id(self) -> str:
        return self.extracted_at.strftime("%Y%m%dT%H%M%S%fZ")

    @property
    def page_counts(self) -> dict[str, int]:
        return {item.table_name: item.page_count for item in self.table_summaries}

    @property
    def starting_cursors(self) -> dict[str, int]:
        return {
            item.table_name: item.starting_cursor for item in self.table_summaries
        }

    @property
    def ending_cursors(self) -> dict[str, int]:
        return {item.table_name: item.ending_cursor for item in self.table_summaries}

    @property
    def page_count(self) -> int:
        return sum(item.page_count for item in self.table_summaries)

    @property
    def source_row_count(self) -> int:
        return sum(item.source_row_count for item in self.table_summaries)


@dataclass(frozen=True)
class IncrementalTableLoadSummary:
    """PostgreSQL outcome for one table in an incremental S3 run."""

    table_name: str
    discovered_pages: int
    loaded_pages: int
    skipped_pages: int
    source_rows: int
    starting_cursor: int | None
    final_cursor: int | None


@dataclass(frozen=True)
class IncrementalLoadSummary:
    """PostgreSQL outcomes for all supported source tables."""

    table_summaries: tuple[IncrementalTableLoadSummary, ...]

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


def _validate_cursor_mapping(
    values: Mapping[str, int],
    *,
    name: str,
) -> dict[str, int]:
    expected = set(SOURCE_COLUMNS)
    actual = set(values)
    if actual != expected:
        raise IncrementalRunError(
            f"{name} did not match the supported source tables; "
            f"missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}."
        )

    validated: dict[str, int] = {}
    for table_name in SOURCE_COLUMNS:
        value = values[table_name]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise IncrementalRunError(
                f"{name} for {table_name} must be a nonnegative integer."
            )
        validated[table_name] = value
    return validated


def _validate_page_count_mapping(values: Mapping[str, int]) -> dict[str, int]:
    return _validate_cursor_mapping(values, name="Expected page counts")


def _validate_advertised_tables(table_names: Sequence[str]) -> None:
    expected = set(SOURCE_COLUMNS)
    actual = set(table_names)
    if len(table_names) != len(actual) or actual != expected:
        raise IncrementalRunError(
            "The API table set did not match the staging contract; "
            f"missing={sorted(expected - actual)}, "
            f"unexpected={sorted(actual - expected)}."
        )


def extract_incremental_run(
    *,
    settings: Settings,
    session: requests.Session,
    writer: S3JsonWriter,
    starting_cursors: Mapping[str, int],
    extracted_at: datetime | None = None,
    output: Callable[[str], None] = print,
) -> IncrementalExtractionSummary:
    """Archive only nonempty API pages after the committed table cursors."""

    cursors = _validate_cursor_mapping(
        starting_cursors,
        name="Starting cursors",
    )
    run_timestamp = extracted_at or datetime.now(timezone.utc)
    if run_timestamp.tzinfo is None:
        raise ValueError("extracted_at must include a timezone.")
    run_timestamp = run_timestamp.astimezone(timezone.utc)

    advertised_tables = get_table_names(
        settings.api_base_url,
        session=session,
        timeout=settings.request_timeout_seconds,
    )
    _validate_advertised_tables(advertised_tables)

    output(
        "Incremental extraction run: "
        f"extract_date={run_timestamp:%Y-%m-%d}, "
        f"run_id={run_timestamp:%Y%m%dT%H%M%S%fZ}"
    )

    summaries: list[IncrementalTableSummary] = []
    for table_name in SOURCE_COLUMNS:
        starting_cursor = cursors[table_name]
        ending_cursor = starting_cursor
        page_count = 0
        source_row_count = 0

        for page in iter_table_pages(
            settings.api_base_url,
            table_name,
            since=starting_cursor,
            limit=settings.api_page_size,
            session=session,
            timeout=settings.request_timeout_seconds,
        ):
            if page.get("table_name") != table_name:
                raise IncrementalRunError(
                    f"{table_name}: API page table_name changed during extraction."
                )
            if page.get("since") != ending_cursor:
                raise IncrementalRunError(
                    f"{table_name}: expected page cursor {ending_cursor}, "
                    f"received {page.get('since')!r}."
                )

            data = page["data"]
            if not data:
                if page_count:
                    raise IncrementalRunError(
                        f"{table_name}: a terminal empty page followed archived pages."
                    )
                ending_cursor = page["next_since"]
                continue

            page_number = page_count + 1
            try:
                writer.write_page(
                    table_name=table_name,
                    load_type="incremental",
                    page_number=page_number,
                    extracted_at=run_timestamp,
                    payload=page,
                )
            except Exception as exc:
                raise IncrementalRunError(
                    f"Failed to archive {table_name} page {page_number}."
                ) from exc

            page_count = page_number
            source_row_count += page["count"]
            ending_cursor = page["next_since"]

        summary = IncrementalTableSummary(
            table_name=table_name,
            starting_cursor=starting_cursor,
            ending_cursor=ending_cursor,
            page_count=page_count,
            source_row_count=source_row_count,
        )
        summaries.append(summary)
        output(
            f"  {table_name}: cursor {starting_cursor:,}->{ending_cursor:,}, "
            f"{page_count:,} pages, {source_row_count:,} source rows"
        )

    result = IncrementalExtractionSummary(
        extracted_at=run_timestamp,
        table_summaries=tuple(summaries),
    )
    output(
        f"  total: {result.page_count:,} pages, "
        f"{result.source_row_count:,} source rows"
    )
    return result


def _actual_page_counts(object_refs: Sequence[S3ObjectRef]) -> dict[str, int]:
    counts = Counter(ref.table_name for ref in object_refs)
    return {table_name: counts[table_name] for table_name in SOURCE_COLUMNS}


def _validate_inventory_counts(
    *,
    actual: Mapping[str, int],
    expected: Mapping[str, int],
) -> None:
    differences = [
        f"{table_name}: expected {expected[table_name]}, found {actual[table_name]}"
        for table_name in SOURCE_COLUMNS
        if actual[table_name] != expected[table_name]
    ]
    if differences:
        raise IncrementalRunError(
            "Incremental S3 inventory did not match the extraction contract; "
            + "; ".join(differences)
            + ". No PostgreSQL load connection was opened."
        )


def discover_and_validate_incremental_run(
    *,
    s3_client: Any,
    bucket_name: str,
    extract_date: str,
    run_id: str,
    expected_page_counts: Mapping[str, int],
    expected_starting_cursors: Mapping[str, int] | None = None,
    expected_ending_cursors: Mapping[str, int] | None = None,
    output: Callable[[str], None] = print,
) -> tuple[StagingPage, ...]:
    """Rediscover and fully validate an exact incremental S3 run."""

    page_counts = _validate_page_count_mapping(expected_page_counts)
    starting_cursors = (
        _validate_cursor_mapping(
            expected_starting_cursors,
            name="Expected starting cursors",
        )
        if expected_starting_cursors is not None
        else None
    )
    ending_cursors = (
        _validate_cursor_mapping(
            expected_ending_cursors,
            name="Expected ending cursors",
        )
        if expected_ending_cursors is not None
        else None
    )

    object_refs = list_incremental_run_objects(
        s3_client=s3_client,
        bucket_name=bucket_name,
        extract_date=extract_date,
        run_id=run_id,
    )
    actual_page_counts = _actual_page_counts(object_refs)
    _validate_inventory_counts(actual=actual_page_counts, expected=page_counts)

    pages = tuple(
        read_staging_page(
            s3_client=s3_client,
            bucket_name=bucket_name,
            object_ref=object_ref,
        )
        for object_ref in object_refs
    )

    for table_name in SOURCE_COLUMNS:
        table_pages = [page for page in pages if page.table_name == table_name]
        if not table_pages:
            if (
                starting_cursors is not None
                and ending_cursors is not None
                and starting_cursors[table_name] != ending_cursors[table_name]
            ):
                raise IncrementalRunError(
                    f"{table_name}: a zero-page run changed its cursor."
                )
            continue

        for index, page in enumerate(table_pages):
            if page.source_load_type != "incremental":
                raise IncrementalRunError(
                    f"{table_name}: archived page was not incremental."
                )
            if page.row_count == 0:
                raise IncrementalRunError(
                    f"{table_name}: incremental S3 runs must not contain empty pages."
                )
            if (
                index
                and page.source_cursor_start
                != table_pages[index - 1].source_cursor_end
            ):
                raise IncrementalRunError(
                    f"{table_name}: cursor continuity failed between pages "
                    f"{index} and {index + 1}."
                )
            should_have_more = index < len(table_pages) - 1
            if page.source_has_more is not should_have_more:
                raise IncrementalRunError(
                    f"{table_name}: has_more did not match the archived page sequence."
                )

        if (
            starting_cursors is not None
            and table_pages[0].source_cursor_start != starting_cursors[table_name]
        ):
            raise IncrementalRunError(
                f"{table_name}: first archived page did not start at the "
                "committed extraction cursor."
            )
        if (
            ending_cursors is not None
            and table_pages[-1].source_cursor_end != ending_cursors[table_name]
        ):
            raise IncrementalRunError(
                f"{table_name}: final archived cursor did not match the "
                "extraction summary."
            )

    output("Incremental S3 inventory and all archived pages are valid.")
    for table_name in SOURCE_COLUMNS:
        output(f"  {table_name}: {actual_page_counts[table_name]:,} objects")
    output(f"  total: {len(pages):,} objects")
    return pages


def load_validated_incremental_pages(
    *,
    pages: Sequence[StagingPage],
    connection_factory: Callable[[], Any],
    starting_cursors: Mapping[str, int] | None = None,
    output: Callable[[str], None] = print,
) -> IncrementalLoadSummary:
    """Apply already archived and validated pages, one transaction per page."""

    validated_starting_cursors = (
        _validate_cursor_mapping(starting_cursors, name="Starting cursors")
        if starting_cursors is not None
        else None
    )

    results: list[PageLoadResult] = []
    if pages:
        connection = connection_factory()
        try:
            for page in pages:
                result = load_staging_page(connection, page)
                results.append(result)
                output(
                    f"  {result.table_name} page {page.source_page_number:05d}: "
                    f"{result.status}, {result.source_row_count:,} source rows, "
                    f"cursor {result.cursor_start:,}->{result.cursor_end:,}"
                )
        finally:
            connection.close()

    summaries: list[IncrementalTableLoadSummary] = []
    for table_name in SOURCE_COLUMNS:
        table_results = [
            result for result in results if result.table_name == table_name
        ]
        table_pages = [page for page in pages if page.table_name == table_name]
        loaded_pages = sum(result.status == "loaded" for result in table_results)
        skipped_pages = sum(result.status == "skipped" for result in table_results)

        if table_pages:
            starting_cursor: int | None = table_pages[0].source_cursor_start
            final_cursor: int | None = table_pages[-1].source_cursor_end
        elif validated_starting_cursors is not None:
            starting_cursor = validated_starting_cursors[table_name]
            final_cursor = starting_cursor
        else:
            starting_cursor = None
            final_cursor = None

        summaries.append(
            IncrementalTableLoadSummary(
                table_name=table_name,
                discovered_pages=len(table_pages),
                loaded_pages=loaded_pages,
                skipped_pages=skipped_pages,
                source_rows=sum(result.source_row_count for result in table_results),
                starting_cursor=starting_cursor,
                final_cursor=final_cursor,
            )
        )

    summary = IncrementalLoadSummary(table_summaries=tuple(summaries))
    output("Incremental staging-load summary:")
    for item in summary.table_summaries:
        cursor_text = (
            "n/a"
            if item.starting_cursor is None
            else f"{item.starting_cursor:,}->{item.final_cursor:,}"
        )
        output(
            f"  {item.table_name}: {item.discovered_pages:,} objects, "
            f"{item.loaded_pages:,} loaded, {item.skipped_pages:,} skipped, "
            f"{item.source_rows:,} source rows, cursor {cursor_text}"
        )
    output(
        f"  total: {summary.discovered_pages:,} objects, "
        f"{summary.loaded_pages:,} loaded, "
        f"{summary.skipped_pages:,} skipped, "
        f"{summary.source_rows:,} source rows"
    )
    return summary


def verify_exact_replay(
    *,
    pages: Sequence[StagingPage],
    connection_factory: Callable[[], Any],
    output: Callable[[str], None] = print,
) -> IncrementalLoadSummary:
    """Replay one exact run and require every archived object to skip."""

    summary = load_validated_incremental_pages(
        pages=pages,
        connection_factory=connection_factory,
        output=output,
    )
    if summary.loaded_pages != 0 or summary.skipped_pages != len(pages):
        raise IncrementalRunError(
            "Exact-run replay was not invariant: every object must be skipped."
        )
    output("Exact-run replay verified: every object skipped.")
    return summary


def _mapping_argument(value: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for item in value.split(","):
        table_name, separator, raw_value = item.strip().partition("=")
        if not separator or table_name not in SOURCE_COLUMNS:
            raise argparse.ArgumentTypeError(
                "Expected comma-separated TABLE=COUNT values for supported tables."
            )
        if table_name in values:
            raise argparse.ArgumentTypeError(
                f"Duplicate table in mapping: {table_name}."
            )
        try:
            number = int(raw_value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                f"Expected a nonnegative integer for {table_name}."
            ) from exc
        if number < 0:
            raise argparse.ArgumentTypeError(
                f"Expected value for {table_name} must be nonnegative."
            )
        values[table_name] = number

    missing = sorted(set(SOURCE_COLUMNS) - set(values))
    if missing:
        raise argparse.ArgumentTypeError(
            "Mapping was missing tables: " + ", ".join(missing)
        )
    return values


def _format_mapping(values: Mapping[str, int]) -> str:
    return ",".join(f"{name}={values[name]}" for name in SOURCE_COLUMNS)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract or resume one manual incremental S3-to-staging run."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Load the fully rediscovered and validated S3 run into PostgreSQL.",
    )
    parser.add_argument(
        "--verify-replay",
        action="store_true",
        help="After a successful apply, replay the exact run and require all skips.",
    )
    parser.add_argument("--resume-extract-date", metavar="YYYY-MM-DD")
    parser.add_argument("--resume-run-id", metavar="RUN_ID")
    parser.add_argument(
        "--expected-page-counts",
        type=_mapping_argument,
        metavar="TABLE=COUNT,...",
        help="Required when resuming; include every table and use zero when empty.",
    )
    return parser


def _read_committed_watermarks(settings: PostgresSettings) -> dict[str, int]:
    connection = connect_postgres(settings)
    try:
        return read_watermarks(connection)
    finally:
        connection.close()


def main() -> None:
    parser = _parser()
    args = parser.parse_args()

    if args.verify_replay and not args.apply:
        parser.error("--verify-replay requires --apply.")

    resume_values = (
        args.resume_extract_date,
        args.resume_run_id,
        args.expected_page_counts,
    )
    if any(value is not None for value in resume_values) and not all(
        value is not None for value in resume_values
    ):
        parser.error(
            "Resuming requires --resume-extract-date, --resume-run-id, "
            "and --expected-page-counts together."
        )

    s3_settings = S3Settings.from_env()
    s3_client = create_s3_client(s3_settings)
    postgres_settings = (
        PostgresSettings.from_env()
        if args.apply or not args.resume_run_id
        else None
    )

    extraction_summary: IncrementalExtractionSummary | None = None
    if args.resume_run_id:
        extract_date = args.resume_extract_date
        run_id = args.resume_run_id
        expected_page_counts = args.expected_page_counts
        starting_cursors = None
        ending_cursors = None
        print(
            "Resuming exact incremental run: "
            f"extract_date={extract_date}, run_id={run_id}"
        )
    else:
        assert postgres_settings is not None
        starting_cursors = _read_committed_watermarks(postgres_settings)
        print("Committed PostgreSQL watermarks:")
        for table_name, cursor in starting_cursors.items():
            print(f"  {table_name}: {cursor:,}")

        api_settings = Settings.from_env()
        writer = S3JsonWriter(
            bucket_name=s3_settings.bucket_name,
            region_name=s3_settings.region,
            profile_name=s3_settings.profile,
        )
        with requests.Session() as session:
            extraction_summary = extract_incremental_run(
                settings=api_settings,
                session=session,
                writer=writer,
                starting_cursors=starting_cursors,
            )

        extract_date = extraction_summary.extract_date
        run_id = extraction_summary.run_id
        expected_page_counts = extraction_summary.page_counts
        ending_cursors = extraction_summary.ending_cursors

    pages = discover_and_validate_incremental_run(
        s3_client=s3_client,
        bucket_name=s3_settings.bucket_name,
        extract_date=extract_date,
        run_id=run_id,
        expected_page_counts=expected_page_counts,
        expected_starting_cursors=starting_cursors,
        expected_ending_cursors=ending_cursors,
    )

    print("Exact-run contract:")
    print(f"  extract_date={extract_date}")
    print(f"  run_id={run_id}")
    print(f"  page_counts={_format_mapping(expected_page_counts)}")

    if not args.apply:
        print("S3 phase complete; PostgreSQL staging was not changed.")
        return

    assert postgres_settings is not None
    connection_factory = lambda: connect_postgres(postgres_settings)
    load_validated_incremental_pages(
        pages=pages,
        connection_factory=connection_factory,
        starting_cursors=starting_cursors,
    )

    if args.verify_replay:
        print("Replaying the exact validated S3 run.")
        verify_exact_replay(
            pages=pages,
            connection_factory=connection_factory,
        )


if __name__ == "__main__":
    main()
