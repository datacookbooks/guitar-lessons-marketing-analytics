"""Environment-backed configuration for the extraction process."""

from __future__ import annotations

from dataclasses import dataclass
import os

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    api_base_url: str
    api_page_size: int = 1_000
    request_timeout_seconds: int = 60

    @classmethod
    def from_env(cls) -> "Settings":
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
