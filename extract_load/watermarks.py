"""Read and validate committed PostgreSQL API cursors."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .staging_rows import SOURCE_COLUMNS


class WatermarkReadError(RuntimeError):
    """Raised when PostgreSQL does not contain one valid cursor per table."""


_READ_WATERMARKS_SQL = """
    SELECT table_name, last_cursor
    FROM staging.etl_watermark
    ORDER BY table_name
"""


def read_watermarks(
    connection: Any,
    *,
    table_names: Iterable[str] = SOURCE_COLUMNS,
) -> dict[str, int]:
    """Return exactly one nonnegative integer cursor for every source table."""

    expected_names = tuple(table_names)
    if not expected_names:
        raise ValueError("At least one source table is required.")
    if len(expected_names) != len(set(expected_names)):
        raise ValueError("Source table names must be unique.")

    with connection.cursor() as cursor:
        cursor.execute(_READ_WATERMARKS_SQL)
        rows = cursor.fetchall()

    found: dict[str, int] = {}
    duplicate_names: list[str] = []

    for row in rows:
        if not isinstance(row, (tuple, list)) or len(row) != 2:
            raise WatermarkReadError("PostgreSQL returned an invalid watermark row.")

        table_name, last_cursor = row
        if not isinstance(table_name, str) or not table_name:
            raise WatermarkReadError("A watermark row had an invalid table name.")
        if table_name in found:
            duplicate_names.append(table_name)
            continue
        if (
            not isinstance(last_cursor, int)
            or isinstance(last_cursor, bool)
            or last_cursor < 0
        ):
            raise WatermarkReadError(
                f"{table_name} did not have a nonnegative integer cursor."
            )
        found[table_name] = last_cursor

    if duplicate_names:
        raise WatermarkReadError(
            "Duplicate watermark rows found for: "
            + ", ".join(sorted(set(duplicate_names)))
            + "."
        )

    expected = set(expected_names)
    actual = set(found)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        raise WatermarkReadError(
            "Watermark table set did not match the supported source tables; "
            f"missing={missing}, unexpected={unexpected}."
        )

    return {table_name: found[table_name] for table_name in expected_names}
