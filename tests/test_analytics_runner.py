from __future__ import annotations

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


def test_apply_closes_connection_when_staging_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    connection = Mock()
    connection.cursor.return_value = cursor
    monkeypatch.setattr(analytics_runner, "staging_state", lambda _: {})

    with pytest.raises(Exception, match="Staging table set changed"):
        analytics_runner.apply_analytics_transform(
            connection=connection,
            verify_rerun=False,
            output=lambda _: None,
        )

    connection.close.assert_called_once()
