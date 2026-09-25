"""Export a standard RDS Secrets Manager document to GitHub Actions."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class SecretConfigurationError(RuntimeError):
    """Raised when the RDS secret does not contain the required fields."""


def postgres_environment(secret: Mapping[str, Any]) -> dict[str, str]:
    """Normalize standard RDS secret keys to the pipeline environment."""

    aliases = {
        "POSTGRES_HOST": ("host",),
        "POSTGRES_PORT": ("port",),
        "POSTGRES_DB": ("dbname", "database", "db"),
        "POSTGRES_USER": ("username", "user"),
        "POSTGRES_PASSWORD": ("password",),
    }
    result: dict[str, str] = {}
    missing: list[str] = []
    for environment_name, candidate_keys in aliases.items():
        value = next(
            (
                secret[key]
                for key in candidate_keys
                if key in secret and secret[key] not in (None, "")
            ),
            None,
        )
        if value is None:
            missing.append("/".join(candidate_keys))
            continue
        text = str(value)
        if "\n" in text or "\r" in text:
            raise SecretConfigurationError(
                f"{environment_name} must not contain a line break."
            )
        result[environment_name] = text

    if missing:
        raise SecretConfigurationError(
            "RDS secret is missing fields: " + ", ".join(missing) + "."
        )
    return result


def _workflow_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main() -> None:
    github_env = os.environ.get("GITHUB_ENV", "").strip()
    if not github_env:
        raise SecretConfigurationError("GITHUB_ENV is not available.")

    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise SecretConfigurationError("RDS secret must be a JSON object.")
    environment = postgres_environment(payload)

    with Path(github_env).open("a", encoding="utf-8") as environment_file:
        for name, value in environment.items():
            print(f"::add-mask::{_workflow_escape(value)}")
            environment_file.write(f"{name}={value}\n")


if __name__ == "__main__":
    main()
