"""Load validated S3 pages into PostgreSQL staging with replay protection."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

import psycopg
from psycopg import sql

from .config import PostgresSettings
from .staging_rows import StagingPage


class StagingLoadError(RuntimeError):
    """Raised when a page cannot be applied without risking inconsistent state."""


class WatermarkMismatchError(StagingLoadError):
    """Raised when a new page does not begin at the committed table cursor."""


@dataclass(frozen=True)
class PageLoadResult:
    table_name: str
    source_s3_key: str
    status: Literal["loaded", "skipped"]
    source_row_count: int
    cursor_start: int
    cursor_end: int


_CLAIM_OBJECT_SQL = """
    INSERT INTO staging.etl_loaded_object (
        source_s3_key,
        table_name,
        source_load_type,
        source_run_id,
        source_page_number,
        source_cursor_start,
        source_cursor_end,
        source_row_count,
        source_has_more,
        extracted_at,
        loaded_at
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (source_s3_key) DO NOTHING
    RETURNING source_s3_key
"""

_LOCK_WATERMARK_SQL = """
    SELECT last_cursor
    FROM staging.etl_watermark
    WHERE table_name = %s
    FOR UPDATE
"""

_ADVANCE_WATERMARK_SQL = """
    UPDATE staging.etl_watermark
    SET
        last_cursor = %s,
        last_successful_run_id = %s,
        last_successful_s3_key = %s,
        updated_at = %s
    WHERE table_name = %s
      AND last_cursor = %s
"""


def connect_postgres(settings: PostgresSettings) -> Any:
    """Open an autocommit connection for explicit transaction blocks."""

    return psycopg.connect(**settings.connection_kwargs())


def _build_staging_insert(page: StagingPage) -> sql.Composed:
    columns = page.insert_columns
    return sql.SQL(
        "INSERT INTO {schema}.{table} ({columns}) "
        "VALUES ({values}) "
        "ON CONFLICT ({raw_row_id}) DO NOTHING"
    ).format(
        schema=sql.Identifier("staging"),
        table=sql.Identifier(page.table_name),
        columns=sql.SQL(", ").join(sql.Identifier(column) for column in columns),
        values=sql.SQL(", ").join(sql.Placeholder(column) for column in columns),
        raw_row_id=sql.Identifier("_raw_row_id"),
    )


def load_staging_page(connection: Any, page: StagingPage) -> PageLoadResult:
    """Apply one page atomically or skip it when its S3 key is already loaded."""

    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            _CLAIM_OBJECT_SQL,
            (
                page.source_s3_key,
                page.table_name,
                page.source_load_type,
                page.source_run_id,
                page.source_page_number,
                page.source_cursor_start,
                page.source_cursor_end,
                page.row_count,
                page.source_has_more,
                page.extracted_at,
                page.loaded_at,
            ),
        )
        claimed = cursor.fetchone()

        if claimed is None:
            return PageLoadResult(
                table_name=page.table_name,
                source_s3_key=page.source_s3_key,
                status="skipped",
                source_row_count=page.row_count,
                cursor_start=page.source_cursor_start,
                cursor_end=page.source_cursor_end,
            )

        cursor.execute(_LOCK_WATERMARK_SQL, (page.table_name,))
        watermark_row = cursor.fetchone()
        if watermark_row is None:
            raise StagingLoadError(f"No watermark row exists for {page.table_name}.")

        current_cursor = watermark_row[0]
        if current_cursor != page.source_cursor_start:
            raise WatermarkMismatchError(
                f"{page.table_name} expected cursor {current_cursor}, "
                f"but {page.source_s3_key} starts at "
                f"{page.source_cursor_start}."
            )

        if page.rows:
            cursor.executemany(
                _build_staging_insert(page),
                page.rows,
            )

        cursor.execute(
            _ADVANCE_WATERMARK_SQL,
            (
                page.source_cursor_end,
                page.source_run_id,
                page.source_s3_key,
                page.loaded_at,
                page.table_name,
                page.source_cursor_start,
            ),
        )
        if cursor.rowcount != 1:
            raise StagingLoadError(
                f"Failed to advance the {page.table_name} watermark safely."
            )

    return PageLoadResult(
        table_name=page.table_name,
        source_s3_key=page.source_s3_key,
        status="loaded",
        source_row_count=page.row_count,
        cursor_start=page.source_cursor_start,
        cursor_end=page.source_cursor_end,
    )


def load_staging_pages(
    connection: Any,
    pages: Iterable[StagingPage],
) -> tuple[PageLoadResult, ...]:
    """Load pages in caller-supplied order using one transaction per page."""

    return tuple(load_staging_page(connection, page) for page in pages)
