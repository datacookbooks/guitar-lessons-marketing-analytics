"""Environment-backed configuration for the extraction process."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date

from dotenv import load_dotenv

load_dotenv()


_RUN_ID = re.compile(r"^(?P<date>\d{8})T\d{12}Z$")


@dataclass(frozen=True)
class Settings:
    api_base_url: str
    api_page_size: int = 1_000
    request_timeout_seconds: int = 60

    @classmethod
    def from_env(cls) -> Settings:
        api_base_url = os.environ.get("API_BASE_URL", "").strip().rstrip("/")

        if not api_base_url:
            raise RuntimeError("API_BASE_URL is not configured.")

        page_size = int(os.environ.get("API_PAGE_SIZE", "1000"))

        if not 1 <= page_size <= 10_000:
            raise RuntimeError("API_PAGE_SIZE must be between 1 and 10000.")

        return cls(
            api_base_url=api_base_url,
            api_page_size=page_size,
        )


@dataclass(frozen=True)
class S3Settings:
    bucket_name: str
    region: str = "us-east-1"
    profile: str | None = None

    @classmethod
    def from_env(cls) -> S3Settings:
        bucket_name = os.environ.get("S3_BUCKET_NAME", "").strip()
        region = os.environ.get("AWS_REGION", "us-east-1").strip()
        profile = os.environ.get("AWS_PROFILE", "").strip() or None

        if not bucket_name:
            raise RuntimeError("S3_BUCKET_NAME is not configured.")

        if not region:
            raise RuntimeError("AWS_REGION is not configured.")

        return cls(
            bucket_name=bucket_name,
            region=region,
            profile=profile,
        )


@dataclass(frozen=True)
class HistoricalRunSettings:
    extract_date: str
    run_id: str

    def __post_init__(self) -> None:
        try:
            parsed_date = date.fromisoformat(self.extract_date)
        except ValueError as exc:
            raise ValueError("S3_HISTORICAL_EXTRACT_DATE must use YYYY-MM-DD.") from exc

        run_match = _RUN_ID.fullmatch(self.run_id)
        if run_match is None:
            raise ValueError("S3_HISTORICAL_RUN_ID must use YYYYMMDDTHHMMSSffffffZ.")

        if run_match["date"] != parsed_date.strftime("%Y%m%d"):
            raise ValueError("The historical extract date must match the run ID date.")

    @classmethod
    def from_env(cls) -> HistoricalRunSettings:
        extract_date = os.environ.get("S3_HISTORICAL_EXTRACT_DATE", "").strip()
        run_id = os.environ.get("S3_HISTORICAL_RUN_ID", "").strip()

        try:
            return cls(
                extract_date=extract_date,
                run_id=run_id,
            )
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc


@dataclass(frozen=True)
class PostgresSettings:
    host: str
    port: int
    database: str
    user: str
    password: str
    sslmode: str = "verify-full"
    sslrootcert: str | None = None
    connect_timeout_seconds: int = 10

    @classmethod
    def from_env(cls) -> PostgresSettings:
        values = {
            "host": os.environ.get("POSTGRES_HOST", "").strip(),
            "database": os.environ.get("POSTGRES_DB", "").strip(),
            "user": os.environ.get("POSTGRES_USER", "").strip(),
            "password": os.environ.get("POSTGRES_PASSWORD", ""),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise RuntimeError(
                "Missing PostgreSQL settings: " + ", ".join(sorted(missing))
            )

        try:
            port = int(os.environ.get("POSTGRES_PORT", "5432"))
            connect_timeout = int(
                os.environ.get("POSTGRES_CONNECT_TIMEOUT_SECONDS", "10")
            )
        except ValueError as exc:
            raise RuntimeError(
                "POSTGRES_PORT and POSTGRES_CONNECT_TIMEOUT_SECONDS must be integers."
            ) from exc

        if not 1 <= port <= 65_535:
            raise RuntimeError("POSTGRES_PORT must be between 1 and 65535.")
        if connect_timeout < 1:
            raise RuntimeError("POSTGRES_CONNECT_TIMEOUT_SECONDS must be at least 1.")

        sslmode = os.environ.get("POSTGRES_SSLMODE", "verify-full").strip()
        allowed_sslmodes = {
            "disable",
            "allow",
            "prefer",
            "require",
            "verify-ca",
            "verify-full",
        }
        if sslmode not in allowed_sslmodes:
            raise RuntimeError("POSTGRES_SSLMODE is not valid.")

        sslrootcert = os.environ.get("POSTGRES_SSLROOTCERT", "").strip() or None
        if sslmode in {"verify-ca", "verify-full"} and sslrootcert is None:
            raise RuntimeError(
                "POSTGRES_SSLROOTCERT is required for certificate verification."
            )

        return cls(
            host=values["host"],
            port=port,
            database=values["database"],
            user=values["user"],
            password=values["password"],
            sslmode=sslmode,
            sslrootcert=sslrootcert,
            connect_timeout_seconds=connect_timeout,
        )

    def connection_kwargs(self) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "host": self.host,
            "port": self.port,
            "dbname": self.database,
            "user": self.user,
            "password": self.password,
            "sslmode": self.sslmode,
            "connect_timeout": self.connect_timeout_seconds,
            "autocommit": True,
        }
        if self.sslrootcert is not None:
            kwargs["sslrootcert"] = self.sslrootcert
        return kwargs
