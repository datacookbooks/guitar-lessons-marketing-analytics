from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import Mock

import pytest

import extract_load.run_analytics_transform as analytics_runner


def test_sql_statements_remove_only_outer_transaction_control(tmp_path: Path) -> None:
    path = tmp_path / "example.sql"
    path.write_text("BEGIN;\nSELECT 1;\nSELECT 2;\nCOMMIT;\n", encoding="utf-8")

    assert analytics_runner.sql_statements(path) == ("SELECT 1", "SELECT 2")


def test_sql_statements_reject_nested_transaction_control(tmp_path: Path) -> None:
    path = tmp_path / "bad.sql"
    path.write_text(
        "BEGIN;\nSELECT 1;\nCOMMIT;\nSELECT 2;\nCOMMIT;\n",
        encoding="utf-8",
    )

    with pytest.raises(
        analytics_runner.AnalyticsTransformError,
        match="Unexpected transaction control",
    ):
        analytics_runner.sql_statements(path)


def test_reconciliation_rejects_unexpected_counts() -> None:
    bad_counts = dict(analytics_runner.EXPECTED_ANALYTICS_COUNTS)
    bad_counts["dim_customer"] -= 1

    with pytest.raises(
        analytics_runner.AnalyticsTransformError,
        match="Analytics row counts",
    ):
        analytics_runner.validate_reconciliation(
            actual_analytics=bad_counts,
            actual_quality=analytics_runner.EXPECTED_QUALITY_COUNTS,
        )


def test_apply_closes_connection_when_baseline_validation_fails() -> None:
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    connection = Mock()
    connection.cursor.return_value = cursor
    cursor.fetchone.side_effect = [(0,)] * 8

    with pytest.raises(
        analytics_runner.AnalyticsTransformError,
        match="Staging-row guardrail",
    ):
        analytics_runner.apply_analytics_transform(
            connection=connection,
            expected_staging_rows=170_145,
            expected_manifest_objects=178,
            verify_rerun=False,
            output=lambda _: None,
        )

    connection.close.assert_called_once()


def test_positive_int_rejects_zero_and_non_integer() -> None:
    for value in ("0", "not-an-integer"):
        with pytest.raises(argparse.ArgumentTypeError):
            analytics_runner._positive_int(value)
