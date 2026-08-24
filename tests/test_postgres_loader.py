from __future__ import annotations

import re
from collections import defaultdict
from contextlib import AbstractContextManager
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import pytest
from psycopg import sql

from extract_load.postgres_loader import (
    StagingLoadError,
    WatermarkMismatchError,
    load_staging_page,
)
from extract_load.staging_rows import s3_document_to_staging_page

SOURCE_KEY = (
    "raw/historical/dim_customer/"
    "extract_date=2026-08-24/"
    "run_id=20260824T191020146277Z/"
    "page-00001.json"
)
LOADED_AT = datetime(2026, 8, 24, 20, 0, tzinfo=timezone.utc)


def customer_page():
    document = {
        "metadata": {
            "table_name": "dim_customer",
            "load_type": "historical",
            "page_number": 1,
            "extracted_at": "2026-08-24T19:10:20.146277Z",
        },
        "response": {
            "table_name": "dim_customer",
            "since": 0,
            "data": [
                {
                    "_raw_row_id": 7,
                    "_generated_for_date": "2025-12-31",
                    "customer_id": "98317178786614",
                    "prospect_key": "O-20250606-0004",
                    "email": "",
                    "signup_timestamp": "2025-06-06 08:11:00+00:00",
                    "state": "GA",
                    "experience_level": "Beginner",
                    "initial_acquisition_channel": "Organic",
                    "first_campaign_id": None,
                    "source_updated_at": "2025-06-06 10:11:00+00:00",
                }
            ],
            "count": 1,
            "next_since": 1006,
            "has_more": True,
        },
    }
    return s3_document_to_staging_page(
        document,
        source_s3_key=SOURCE_KEY,
        loaded_at=LOADED_AT,
    )


class FakeTransaction(AbstractContextManager):
    def __init__(self, connection: FakeConnection):
        self.connection = connection
        self.snapshot: dict[str, Any] | None = None

    def __enter__(self):
        self.snapshot = {
            "manifests": deepcopy(self.connection.manifests),
            "watermarks": deepcopy(self.connection.watermarks),
            "staged_rows": deepcopy(self.connection.staged_rows),
        }
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if exc_type is None:
            self.connection.commits += 1
            return False

        assert self.snapshot is not None
        self.connection.manifests = self.snapshot["manifests"]
        self.connection.watermarks = self.snapshot["watermarks"]
        self.connection.staged_rows = self.snapshot["staged_rows"]
        self.connection.rollbacks += 1
        return False


class FakeCursor(AbstractContextManager):
    def __init__(self, connection: FakeConnection):
        self.connection = connection
        self._fetchone = None
        self.rowcount = -1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        return False

    @staticmethod
    def _query_text(query: Any) -> str:
        rendered = query.as_string() if isinstance(query, sql.Composable) else query
        return " ".join(rendered.split())

    def execute(self, query: Any, params: tuple) -> None:
        query_text = self._query_text(query)

        if query_text.startswith("INSERT INTO staging.etl_loaded_object"):
            source_s3_key = params[0]
            if source_s3_key in self.connection.manifests:
                self._fetchone = None
                self.rowcount = 0
            else:
                self.connection.manifests.add(source_s3_key)
                self._fetchone = (source_s3_key,)
                self.rowcount = 1
            return

        if query_text.startswith("SELECT last_cursor"):
            table_name = params[0]
            current = self.connection.watermarks.get(table_name)
            self._fetchone = None if current is None else (current,)
            self.rowcount = 0 if current is None else 1
            return

        if query_text.startswith("UPDATE staging.etl_watermark"):
            cursor_end, _, _, _, table_name, cursor_start = params
            if (
                not self.connection.fail_on_watermark_update
                and self.connection.watermarks.get(table_name) == cursor_start
            ):
                self.connection.watermarks[table_name] = cursor_end
                self.rowcount = 1
            else:
                self.rowcount = 0
            self._fetchone = None
            return

        raise AssertionError(f"Unexpected SQL: {query_text}")

    def executemany(self, query: Any, params_seq) -> None:
        if self.connection.fail_on_staging_insert:
            raise RuntimeError("simulated staging insert failure")

        query_text = self._query_text(query)
        assert 'ON CONFLICT ("_raw_row_id") DO NOTHING' in query_text
        match = re.search(r'INSERT INTO "staging"\."([^"]+)"', query_text)
        assert match is not None
        table_name = match.group(1)
        rows = list(params_seq)
        self.connection.staged_rows[table_name].update(
            row["_raw_row_id"] for row in rows
        )
        self.rowcount = len(rows)
        self._fetchone = None

    def fetchone(self):
        return self._fetchone


class FakeConnection:
    def __init__(self):
        self.manifests: set[str] = set()
        self.watermarks: dict[str, int] = {"dim_customer": 0}
        self.staged_rows: dict[str, set[str]] = defaultdict(set)
        self.fail_on_staging_insert = False
        self.fail_on_watermark_update = False
        self.commits = 0
        self.rollbacks = 0

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)


def test_load_page_commits_rows_manifest_and_watermark() -> None:
    connection = FakeConnection()

    result = load_staging_page(connection, customer_page())

    assert result.status == "loaded"
    assert connection.manifests == {SOURCE_KEY}
    assert connection.staged_rows["dim_customer"] == {"7"}
    assert connection.watermarks["dim_customer"] == 1006
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_replay_skips_an_already_loaded_s3_key() -> None:
    connection = FakeConnection()
    connection.manifests.add(SOURCE_KEY)
    connection.watermarks["dim_customer"] = 1006

    result = load_staging_page(connection, customer_page())

    assert result.status == "skipped"
    assert connection.staged_rows["dim_customer"] == set()
    assert connection.watermarks["dim_customer"] == 1006
    assert connection.commits == 1


def test_staging_failure_rolls_back_manifest_rows_and_watermark() -> None:
    connection = FakeConnection()
    connection.fail_on_staging_insert = True

    with pytest.raises(RuntimeError, match="simulated"):
        load_staging_page(connection, customer_page())

    assert connection.manifests == set()
    assert connection.staged_rows["dim_customer"] == set()
    assert connection.watermarks["dim_customer"] == 0
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_unexpected_watermark_rolls_back_the_object_claim() -> None:
    connection = FakeConnection()
    connection.watermarks["dim_customer"] = 99

    with pytest.raises(WatermarkMismatchError, match="expected cursor 99"):
        load_staging_page(connection, customer_page())

    assert connection.manifests == set()
    assert connection.staged_rows["dim_customer"] == set()
    assert connection.watermarks["dim_customer"] == 99
    assert connection.rollbacks == 1


def test_failed_watermark_update_rolls_back_rows_and_object_claim() -> None:
    connection = FakeConnection()
    connection.fail_on_watermark_update = True

    with pytest.raises(StagingLoadError, match="advance"):
        load_staging_page(connection, customer_page())

    assert connection.manifests == set()
    assert connection.staged_rows["dim_customer"] == set()
    assert connection.watermarks["dim_customer"] == 0
    assert connection.rollbacks == 1
