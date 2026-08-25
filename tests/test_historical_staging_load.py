from __future__ import annotations

import argparse
from unittest.mock import Mock

import pytest

import extract_load.run_historical_staging_load as historical_load
from extract_load.config import HistoricalRunSettings
from extract_load.postgres_loader import PageLoadResult
from extract_load.s3_reader import S3ObjectRef


RUN = HistoricalRunSettings(
    extract_date="2026-08-24",
    run_id="20260824T191020146277Z",
)


def object_ref(table_name: str, page_number: int) -> S3ObjectRef:
    return S3ObjectRef(
        table_name=table_name,
        page_number=page_number,
        key=(
            f"raw/historical/{table_name}/"
            "extract_date=2026-08-24/"
            "run_id=20260824T191020146277Z/"
            f"page-{page_number:05d}.json"
        ),
    )


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_inventory_mismatch_stops_before_postgres_connection(monkeypatch) -> None:
    refs = (object_ref("dim_plan", 1),)
    monkeypatch.setattr(
        historical_load,
        "list_historical_run_objects",
        Mock(return_value=refs),
    )
    connection_factory = Mock()

    with pytest.raises(
        historical_load.HistoricalStagingLoadError,
        match="No PostgreSQL connection was opened",
    ):
        historical_load.run_historical_staging_load(
            s3_client=object(),
            bucket_name="test-bucket",
            historical_run=RUN,
            expected_page_counts={"dim_plan": 2},
            connection_factory=connection_factory,
            output=lambda _: None,
        )

    connection_factory.assert_not_called()


def test_orchestration_reports_loaded_and_skipped_pages(monkeypatch) -> None:
    refs = (
        object_ref("dim_plan", 1),
        object_ref("dim_customer", 1),
        object_ref("dim_customer", 2),
    )
    monkeypatch.setattr(
        historical_load,
        "list_historical_run_objects",
        Mock(return_value=refs),
    )

    pages = {ref.key: object() for ref in refs}
    monkeypatch.setattr(
        historical_load,
        "read_staging_page",
        Mock(side_effect=lambda **kwargs: pages[kwargs["object_ref"].key]),
    )

    results = iter(
        (
            PageLoadResult(
                table_name="dim_plan",
                source_s3_key=refs[0].key,
                status="skipped",
                source_row_count=3,
                cursor_start=0,
                cursor_end=3,
            ),
            PageLoadResult(
                table_name="dim_customer",
                source_s3_key=refs[1].key,
                status="loaded",
                source_row_count=1_000,
                cursor_start=0,
                cursor_end=1_000,
            ),
            PageLoadResult(
                table_name="dim_customer",
                source_s3_key=refs[2].key,
                status="loaded",
                source_row_count=250,
                cursor_start=1_000,
                cursor_end=1_250,
            ),
        )
    )
    monkeypatch.setattr(
        historical_load,
        "load_staging_page",
        Mock(side_effect=lambda connection, page: next(results)),
    )

    connection = FakeConnection()
    output: list[str] = []
    summary = historical_load.run_historical_staging_load(
        s3_client=object(),
        bucket_name="test-bucket",
        historical_run=RUN,
        expected_page_counts={"dim_plan": 1, "dim_customer": 2},
        connection_factory=lambda: connection,
        output=output.append,
    )

    assert connection.closed is True
    assert summary.discovered_pages == 3
    assert summary.loaded_pages == 2
    assert summary.skipped_pages == 1
    assert summary.source_rows == 1_253

    dim_plan, dim_customer = summary.table_summaries[:2]
    assert dim_plan.skipped_pages == 1
    assert dim_plan.skipped_source_rows == 3
    assert dim_customer.loaded_pages == 2
    assert dim_customer.loaded_source_rows == 1_250
    assert dim_customer.final_cursor == 1_250
    assert any("Inventory matches" in line for line in output)
    assert any("dim_plan page 00001: skipped" in line for line in output)


def test_connection_closes_when_a_page_load_fails(monkeypatch) -> None:
    refs = (object_ref("dim_plan", 1),)
    monkeypatch.setattr(
        historical_load,
        "list_historical_run_objects",
        Mock(return_value=refs),
    )
    monkeypatch.setattr(historical_load, "read_staging_page", Mock(return_value=object()))
    monkeypatch.setattr(
        historical_load,
        "load_staging_page",
        Mock(side_effect=RuntimeError("simulated load failure")),
    )
    connection = FakeConnection()

    with pytest.raises(RuntimeError, match="simulated load failure"):
        historical_load.run_historical_staging_load(
            s3_client=object(),
            bucket_name="test-bucket",
            historical_run=RUN,
            expected_page_counts={"dim_plan": 1},
            connection_factory=lambda: connection,
            output=lambda _: None,
        )

    assert connection.closed is True


def test_cli_expected_page_counts_require_every_source_table() -> None:
    with pytest.raises(
        argparse.ArgumentTypeError,
        match="missing tables",
    ):
        historical_load._expected_page_counts("dim_plan=1")
