from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest

import extract_load.run_reporting_views as reporting_runner


def test_sql_statements_ignore_comment_semicolons(tmp_path: Path) -> None:
    path = tmp_path / "example.sql"
    path.write_text(
        "BEGIN;\n-- explanation; not SQL\nSELECT 1;\nSELECT 2;\nCOMMIT;\n",
        encoding="utf-8",
    )

    assert reporting_runner.sql_statements(path) == ("SELECT 1", "SELECT 2")


def test_sql_statements_reject_nested_transaction_control(tmp_path: Path) -> None:
    path = tmp_path / "bad.sql"
    path.write_text(
        "BEGIN;\nSELECT 1;\nCOMMIT;\nSELECT 2;\nCOMMIT;\n",
        encoding="utf-8",
    )

    with pytest.raises(
        reporting_runner.ReportingViewError,
        match="Unexpected transaction control",
    ):
        reporting_runner.sql_statements(path)


def test_source_guardrails_reject_changed_cutoff() -> None:
    with pytest.raises(
        reporting_runner.ReportingViewError,
        match="cutoff guardrail",
    ):
        reporting_runner.validate_source_guardrails(
            actual_analytics=reporting_runner.EXPECTED_ANALYTICS_COUNTS,
            actual_data_through_date=date(2026, 8, 26),
            expected_data_through_date=date(2026, 8, 25),
        )


def test_source_guardrails_reject_changed_analytics_counts() -> None:
    changed = dict(reporting_runner.EXPECTED_ANALYTICS_COUNTS)
    changed["dim_customer"] += 1

    with pytest.raises(
        reporting_runner.ReportingViewError,
        match="Analytics source counts",
    ):
        reporting_runner.validate_source_guardrails(
            actual_analytics=changed,
            actual_data_through_date=date(2026, 8, 25),
            expected_data_through_date=date(2026, 8, 25),
        )


def test_reconciliation_rejects_changed_component() -> None:
    changed = dict(reporting_runner.EXPECTED_RECONCILIATION)
    changed["incremental_windows"] += 1

    with pytest.raises(
        reporting_runner.ReportingViewError,
        match="Reporting components",
    ):
        reporting_runner.validate_reconciliation(changed)


def test_apply_closes_connection_when_source_guardrail_fails() -> None:
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    connection = Mock()
    connection.cursor.return_value = cursor
    cursor.fetchone.side_effect = [
        *((count,) for count in reporting_runner.EXPECTED_ANALYTICS_COUNTS.values()),
        (date(2026, 8, 26),),
    ]

    with pytest.raises(
        reporting_runner.ReportingViewError,
        match="cutoff guardrail",
    ):
        reporting_runner.apply_reporting_views(
            connection=connection,
            expected_data_through_date=date(2026, 8, 25),
            verify_rerun=False,
            output=lambda _: None,
        )

    connection.close.assert_called_once()


def test_iso_date_rejects_invalid_value() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        reporting_runner._iso_date("08/25/2026")


def test_reviewed_scripts_contain_seventeen_views() -> None:
    statements = (
        reporting_runner.sql_statements(reporting_runner.HELPER_SQL_PATH)
        + reporting_runner.sql_statements(reporting_runner.REPORTING_SQL_PATH)
    )

    assert len(statements) == 17
    assert all(
        statement.upper().startswith("CREATE OR REPLACE VIEW")
        for statement in statements
    )
