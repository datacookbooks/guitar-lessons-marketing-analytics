from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

import extract_load.run_bi_access as bi_runner


def test_sql_body_removes_outer_transaction_commands(
    tmp_path: Path,
) -> None:
    path = tmp_path / "example.sql"
    path.write_text(
        """
        BEGIN;

        DO $$
        BEGIN
            PERFORM 1;
        END
        $$;

        COMMIT;
        """,
        encoding="utf-8",
    )

    body = bi_runner.sql_body(path)

    assert not body.upper().startswith("BEGIN;")
    assert not body.upper().endswith("COMMIT;")
    assert "DO $$" in body
    assert "PERFORM 1;" in body


def test_sql_body_rejects_additional_transaction_control(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bad.sql"
    path.write_text(
        """
        BEGIN;
        SELECT 1;
        COMMIT;
        SELECT 2;
        COMMIT;
        """,
        encoding="utf-8",
    )

    with pytest.raises(
        bi_runner.BIAccessError,
        match="Unexpected transaction control",
    ):
        bi_runner.sql_body(path)


def test_verify_bi_role_accepts_exact_privilege_design() -> None:
    cursor = Mock()
    cursor.fetchone.side_effect = [
        ("marketing_analytics",),
        (False, False, False, False, False, False, False),
        (True, False),
        (True, False),
        (True, False),
        (False, False),
        *((True,) for _ in bi_runner.APPROVED_RELATIONS),
        *((False,) for _ in bi_runner.DENIED_RELATIONS),
    ]

    bi_runner.verify_bi_role(
        cursor,
        expected_database="marketing_analytics",
    )


def test_verify_bi_role_rejects_wrong_database() -> None:
    cursor = Mock()
    cursor.fetchone.return_value = ("another_database",)

    with pytest.raises(
        bi_runner.BIAccessError,
        match="Database guardrail failed",
    ):
        bi_runner.verify_bi_role(
            cursor,
            expected_database="marketing_analytics",
        )


def test_apply_bi_access_commits_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)

    connection = Mock()
    connection.cursor.return_value = cursor

    monkeypatch.setattr(
        bi_runner,
        "sql_body",
        lambda: "SELECT 1",
    )
    verify = Mock()
    monkeypatch.setattr(
        bi_runner,
        "verify_bi_role",
        verify,
    )

    bi_runner.apply_bi_access(
        connection=connection,
        expected_database="marketing_analytics",
        output=lambda _: None,
    )

    cursor.execute.assert_called_once_with("SELECT 1")
    verify.assert_called_once_with(
        cursor,
        expected_database="marketing_analytics",
    )
    connection.commit.assert_called_once()
    connection.rollback.assert_not_called()
    connection.close.assert_called_once()


def test_apply_bi_access_rolls_back_and_closes_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)

    connection = Mock()
    connection.cursor.return_value = cursor

    monkeypatch.setattr(
        bi_runner,
        "sql_body",
        lambda: "SELECT 1",
    )
    monkeypatch.setattr(
        bi_runner,
        "verify_bi_role",
        Mock(side_effect=bi_runner.BIAccessError("verification failed")),
    )

    with pytest.raises(
        bi_runner.BIAccessError,
        match="verification failed",
    ):
        bi_runner.apply_bi_access(
            connection=connection,
            expected_database="marketing_analytics",
            output=lambda _: None,
        )

    connection.commit.assert_not_called()
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()
