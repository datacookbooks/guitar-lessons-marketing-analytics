from __future__ import annotations

import pytest

from scripts.export_rds_secret import (
    SecretConfigurationError,
    postgres_environment,
)


def test_postgres_environment_accepts_standard_rds_secret() -> None:
    assert postgres_environment(
        {
            "host": "database.example.com",
            "port": 5432,
            "dbname": "marketing_analytics",
            "username": "etl_runner",
            "password": "secret-value",
        }
    ) == {
        "POSTGRES_HOST": "database.example.com",
        "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "marketing_analytics",
        "POSTGRES_USER": "etl_runner",
        "POSTGRES_PASSWORD": "secret-value",
    }


def test_postgres_environment_accepts_local_config_aliases() -> None:
    environment = postgres_environment(
        {
            "host": "database.example.com",
            "port": 5432,
            "database": "marketing_analytics",
            "user": "etl_runner",
            "password": "secret-value",
        }
    )

    assert environment["POSTGRES_DB"] == "marketing_analytics"
    assert environment["POSTGRES_USER"] == "etl_runner"


def test_postgres_environment_rejects_missing_password() -> None:
    with pytest.raises(SecretConfigurationError, match="password"):
        postgres_environment(
            {
                "host": "database.example.com",
                "port": 5432,
                "dbname": "marketing_analytics",
                "username": "etl_runner",
            }
        )
