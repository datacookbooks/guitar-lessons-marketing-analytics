from __future__ import annotations

import pytest

from extract_load.staging_rows import SOURCE_COLUMNS
from extract_load.watermarks import WatermarkReadError, read_watermarks


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, query):
        self.executed = query

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows):
        self.cursor_instance = FakeCursor(rows)

    def cursor(self):
        return self.cursor_instance


def valid_rows():
    return [(table_name, index) for index, table_name in enumerate(SOURCE_COLUMNS)]


def test_reads_one_cursor_for_every_supported_table() -> None:
    connection = FakeConnection(reversed(valid_rows()))

    result = read_watermarks(connection)

    assert list(result) == list(SOURCE_COLUMNS)
    assert result["dim_plan"] == 0
    assert result["fact_campaign_assignment"] == 6
    assert "FROM staging.etl_watermark" in connection.cursor_instance.executed


def test_rejects_a_missing_watermark() -> None:
    with pytest.raises(WatermarkReadError, match="missing=.*dim_plan"):
        read_watermarks(FakeConnection(valid_rows()[1:]))


def test_rejects_duplicate_watermarks() -> None:
    rows = valid_rows() + [("dim_plan", 999)]

    with pytest.raises(WatermarkReadError, match="Duplicate watermark"):
        read_watermarks(FakeConnection(rows))


@pytest.mark.parametrize("invalid_cursor", [-1, True, "7", None])
def test_rejects_an_invalid_cursor(invalid_cursor) -> None:
    rows = valid_rows()
    rows[0] = ("dim_plan", invalid_cursor)

    with pytest.raises(WatermarkReadError, match="nonnegative integer"):
        read_watermarks(FakeConnection(rows))
