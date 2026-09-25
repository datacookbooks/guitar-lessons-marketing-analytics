from __future__ import annotations

from unittest.mock import Mock

import pytest

from extract_load.run_daily_pipeline import (
    DAILY_PIPELINE_LOCK_ID,
    DailyPipelineError,
    acquire_pipeline_lock,
    validate_final_watermarks,
    validate_source_tables,
)
from extract_load.staging_rows import SOURCE_COLUMNS


def test_source_table_contract_accepts_all_supported_tables() -> None:
    validate_source_tables(list(SOURCE_COLUMNS))


def test_source_table_contract_rejects_missing_table() -> None:
    with pytest.raises(DailyPipelineError, match="missing"):
        validate_source_tables(list(SOURCE_COLUMNS)[:-1])


def test_advisory_lock_rejects_overlapping_run() -> None:
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    cursor.fetchone.return_value = (False,)
    connection = Mock()
    connection.cursor.return_value = cursor

    with pytest.raises(DailyPipelineError, match="already holds"):
        acquire_pipeline_lock(connection)

    cursor.execute.assert_called_once_with(
        "SELECT pg_try_advisory_lock(%s)",
        (DAILY_PIPELINE_LOCK_ID,),
    )


def test_final_watermarks_must_match_extraction() -> None:
    with pytest.raises(DailyPipelineError, match="did not match"):
        validate_final_watermarks(
            actual={"dim_customer": 10},
            expected={"dim_customer": 11},
        )
