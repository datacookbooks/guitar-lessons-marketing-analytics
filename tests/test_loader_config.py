import pytest

from extract_load.config import HistoricalRunSettings, PostgresSettings


def test_historical_run_settings_validate_matching_dates(monkeypatch) -> None:
    monkeypatch.setenv("S3_HISTORICAL_EXTRACT_DATE", "2026-08-24")
    monkeypatch.setenv("S3_HISTORICAL_RUN_ID", "20260824T191020146277Z")

    settings = HistoricalRunSettings.from_env()

    assert settings.extract_date == "2026-08-24"
    assert settings.run_id == "20260824T191020146277Z"


def test_historical_run_settings_reject_mismatched_dates(monkeypatch) -> None:
    monkeypatch.setenv("S3_HISTORICAL_EXTRACT_DATE", "2026-08-23")
    monkeypatch.setenv("S3_HISTORICAL_RUN_ID", "20260824T191020146277Z")

    with pytest.raises(RuntimeError, match="must match"):
        HistoricalRunSettings.from_env()


def test_historical_run_settings_validate_direct_construction() -> None:
    with pytest.raises(ValueError, match="must match"):
        HistoricalRunSettings(
            extract_date="2026-08-23",
            run_id="20260824T191020146277Z",
        )


def test_postgres_settings_build_secure_connection_kwargs(monkeypatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "database.example.test")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_DB", "marketing_analytics")
    monkeypatch.setenv("POSTGRES_USER", "loader")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret-for-test")
    monkeypatch.setenv("POSTGRES_SSLMODE", "verify-full")
    monkeypatch.setenv("POSTGRES_SSLROOTCERT", "/certs/rds.pem")
    monkeypatch.setenv("POSTGRES_CONNECT_TIMEOUT_SECONDS", "12")

    settings = PostgresSettings.from_env()

    assert settings.connection_kwargs() == {
        "host": "database.example.test",
        "port": 5432,
        "dbname": "marketing_analytics",
        "user": "loader",
        "password": "secret-for-test",
        "sslmode": "verify-full",
        "sslrootcert": "/certs/rds.pem",
        "connect_timeout": 12,
        "autocommit": True,
    }


def test_postgres_settings_require_root_cert_for_verification(monkeypatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "database.example.test")
    monkeypatch.setenv("POSTGRES_DB", "marketing_analytics")
    monkeypatch.setenv("POSTGRES_USER", "loader")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret-for-test")
    monkeypatch.setenv("POSTGRES_SSLMODE", "verify-full")
    monkeypatch.delenv("POSTGRES_SSLROOTCERT", raising=False)

    with pytest.raises(RuntimeError, match="SSLROOTCERT"):
        PostgresSettings.from_env()
