from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

import extract_load.run_incremental_load as incremental
from extract_load.config import Settings
from extract_load.postgres_loader import PageLoadResult
from extract_load.s3_reader import S3ObjectRef
from extract_load.staging_rows import SOURCE_COLUMNS, StagingPage


RUN_TIMESTAMP = datetime(2026, 8, 25, 17, 0, 0, 123456, tzinfo=timezone.utc)
RUN_ID = "20260825T170000123456Z"


def cursors(value: int = 10) -> dict[str, int]:
    return {table_name: value for table_name in SOURCE_COLUMNS}


def empty_page(table_name: str, since: int) -> dict:
    return {
        "table_name": table_name,
        "since": since,
        "data": [],
        "count": 0,
        "next_since": since,
        "has_more": False,
    }


def api_page(
    table_name: str,
    *,
    since: int,
    next_since: int,
    has_more: bool,
) -> dict:
    return {
        "table_name": table_name,
        "since": since,
        "data": [{"_raw_row_id": next_since}],
        "count": 1,
        "next_since": next_since,
        "has_more": has_more,
    }


def object_ref(table_name: str, page_number: int) -> S3ObjectRef:
    return S3ObjectRef(
        table_name=table_name,
        page_number=page_number,
        key=(
            f"raw/incremental/{table_name}/"
            "extract_date=2026-08-25/"
            f"run_id={RUN_ID}/"
            f"page-{page_number:05d}.json"
        ),
    )


def staging_page(
    table_name: str,
    page_number: int,
    *,
    cursor_start: int,
    cursor_end: int,
    has_more: bool,
) -> StagingPage:
    return StagingPage(
        table_name=table_name,
        source_s3_key=object_ref(table_name, page_number).key,
        source_run_id=RUN_ID,
        source_load_type="incremental",
        source_page_number=page_number,
        source_cursor_start=cursor_start,
        source_cursor_end=cursor_end,
        source_has_more=has_more,
        extracted_at=RUN_TIMESTAMP,
        loaded_at=RUN_TIMESTAMP,
        rows=({"_raw_row_id": str(cursor_end)},),
    )


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_incremental_extraction_writes_only_nonempty_pages(monkeypatch) -> None:
    monkeypatch.setattr(
        incremental,
        "get_table_names",
        Mock(return_value=list(reversed(SOURCE_COLUMNS))),
    )

    def pages_for_table(base_url, table_name, *, since, **kwargs):
        if table_name == "dim_plan":
            return iter(
                [
                    api_page(
                        table_name,
                        since=since,
                        next_since=11,
                        has_more=True,
                    ),
                    api_page(
                        table_name,
                        since=11,
                        next_since=12,
                        has_more=False,
                    ),
                ]
            )
        return iter([empty_page(table_name, since)])

    monkeypatch.setattr(incremental, "iter_table_pages", pages_for_table)
    writer = Mock()

    summary = incremental.extract_incremental_run(
        settings=Settings(api_base_url="https://example.test"),
        session=Mock(),
        writer=writer,
        starting_cursors=cursors(),
        extracted_at=RUN_TIMESTAMP,
        output=lambda _: None,
    )

    assert writer.write_page.call_count == 2
    assert all(
        call.kwargs["load_type"] == "incremental"
        for call in writer.write_page.call_args_list
    )
    assert summary.page_counts["dim_plan"] == 2
    assert summary.ending_cursors["dim_plan"] == 12
    assert summary.page_counts["fact_payment"] == 0
    assert summary.ending_cursors["fact_payment"] == 10
    assert len(summary.table_summaries) == len(SOURCE_COLUMNS)


def test_incremental_extraction_rejects_cursor_discontinuity(monkeypatch) -> None:
    monkeypatch.setattr(
        incremental,
        "get_table_names",
        Mock(return_value=list(SOURCE_COLUMNS)),
    )
    monkeypatch.setattr(
        incremental,
        "iter_table_pages",
        Mock(
            return_value=iter(
                [
                    api_page(
                        "dim_plan",
                        since=999,
                        next_since=1_000,
                        has_more=False,
                    )
                ]
            )
        ),
    )

    with pytest.raises(incremental.IncrementalRunError, match="expected page cursor"):
        incremental.extract_incremental_run(
            settings=Settings(api_base_url="https://example.test"),
            session=Mock(),
            writer=Mock(),
            starting_cursors=cursors(),
            extracted_at=RUN_TIMESTAMP,
            output=lambda _: None,
        )


def test_discovers_zero_table_run_and_validates_page_continuity(monkeypatch) -> None:
    refs = (object_ref("dim_plan", 1), object_ref("dim_plan", 2))
    pages = {
        refs[0].key: staging_page(
            "dim_plan",
            1,
            cursor_start=10,
            cursor_end=11,
            has_more=True,
        ),
        refs[1].key: staging_page(
            "dim_plan",
            2,
            cursor_start=11,
            cursor_end=12,
            has_more=False,
        ),
    }
    monkeypatch.setattr(
        incremental,
        "list_incremental_run_objects",
        Mock(return_value=refs),
    )
    monkeypatch.setattr(
        incremental,
        "read_staging_page",
        Mock(side_effect=lambda **kwargs: pages[kwargs["object_ref"].key]),
    )
    page_counts = cursors(0)
    page_counts["dim_plan"] = 2
    ending = cursors()
    ending["dim_plan"] = 12

    result = incremental.discover_and_validate_incremental_run(
        s3_client=object(),
        bucket_name="test-bucket",
        extract_date="2026-08-25",
        run_id=RUN_ID,
        expected_page_counts=page_counts,
        expected_starting_cursors=cursors(),
        expected_ending_cursors=ending,
        output=lambda _: None,
    )

    assert result == tuple(pages.values())


def test_inventory_mismatch_prevents_page_reads(monkeypatch) -> None:
    monkeypatch.setattr(
        incremental,
        "list_incremental_run_objects",
        Mock(return_value=(object_ref("dim_plan", 1),)),
    )
    read_page = Mock()
    monkeypatch.setattr(incremental, "read_staging_page", read_page)

    with pytest.raises(incremental.IncrementalRunError, match="No PostgreSQL"):
        incremental.discover_and_validate_incremental_run(
            s3_client=object(),
            bucket_name="test-bucket",
            extract_date="2026-08-25",
            run_id=RUN_ID,
            expected_page_counts=cursors(0),
            output=lambda _: None,
        )

    read_page.assert_not_called()


def test_zero_object_load_does_not_open_postgres() -> None:
    connection_factory = Mock()

    summary = incremental.load_validated_incremental_pages(
        pages=(),
        connection_factory=connection_factory,
        starting_cursors=cursors(),
        output=lambda _: None,
    )

    connection_factory.assert_not_called()
    assert summary.discovered_pages == 0
    assert summary.loaded_pages == 0
    assert all(
        item.starting_cursor == item.final_cursor
        for item in summary.table_summaries
    )


def test_partial_load_can_resume_the_exact_archived_run(monkeypatch) -> None:
    pages = (
        staging_page(
            "dim_plan", 1, cursor_start=10, cursor_end=11, has_more=True
        ),
        staging_page(
            "dim_plan", 2, cursor_start=11, cursor_end=12, has_more=False
        ),
    )
    first_connection = FakeConnection()
    first_results = iter(
        [
            PageLoadResult(
                table_name="dim_plan",
                source_s3_key=pages[0].source_s3_key,
                status="loaded",
                source_row_count=1,
                cursor_start=10,
                cursor_end=11,
            ),
            RuntimeError("simulated second-page failure"),
        ]
    )

    def first_load(connection, page):
        result = next(first_results)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(incremental, "load_staging_page", first_load)
    with pytest.raises(RuntimeError, match="second-page failure"):
        incremental.load_validated_incremental_pages(
            pages=pages,
            connection_factory=lambda: first_connection,
            output=lambda _: None,
        )
    assert first_connection.closed is True

    resume_results = iter(
        [
            PageLoadResult(
                table_name="dim_plan",
                source_s3_key=pages[0].source_s3_key,
                status="skipped",
                source_row_count=1,
                cursor_start=10,
                cursor_end=11,
            ),
            PageLoadResult(
                table_name="dim_plan",
                source_s3_key=pages[1].source_s3_key,
                status="loaded",
                source_row_count=1,
                cursor_start=11,
                cursor_end=12,
            ),
        ]
    )
    monkeypatch.setattr(
        incremental,
        "load_staging_page",
        Mock(side_effect=lambda connection, page: next(resume_results)),
    )

    summary = incremental.load_validated_incremental_pages(
        pages=pages,
        connection_factory=FakeConnection,
        output=lambda _: None,
    )

    assert summary.loaded_pages == 1
    assert summary.skipped_pages == 1


def test_exact_run_replay_requires_every_object_to_skip(monkeypatch) -> None:
    page = staging_page(
        "dim_plan", 1, cursor_start=10, cursor_end=11, has_more=False
    )
    monkeypatch.setattr(
        incremental,
        "load_staging_page",
        Mock(
            return_value=PageLoadResult(
                table_name="dim_plan",
                source_s3_key=page.source_s3_key,
                status="skipped",
                source_row_count=1,
                cursor_start=10,
                cursor_end=11,
            )
        ),
    )

    summary = incremental.verify_exact_replay(
        pages=(page,),
        connection_factory=FakeConnection,
        output=lambda _: None,
    )

    assert summary.loaded_pages == 0
    assert summary.skipped_pages == 1
