"""Write raw API response pages to Amazon S3."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any

import boto3


_VALID_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("extracted_at must include a timezone.")

    return value.astimezone(timezone.utc)


def build_raw_key(
    *,
    table_name: str,
    load_type: str,
    page_number: int,
    extracted_at: datetime,
) -> str:
    """Build a unique, organized S3 key for one API response page."""

    if not _VALID_NAME.fullmatch(table_name):
        raise ValueError(f"Invalid table name: {table_name!r}")

    if not _VALID_NAME.fullmatch(load_type):
        raise ValueError(f"Invalid load type: {load_type!r}")

    if page_number < 1:
        raise ValueError("page_number must be at least 1.")

    timestamp = _as_utc(extracted_at)
    extract_date = timestamp.strftime("%Y-%m-%d")
    run_id = timestamp.strftime("%Y%m%dT%H%M%S%fZ")

    return (
        f"raw/{load_type}/{table_name}/"
        f"extract_date={extract_date}/"
        f"run_id={run_id}/"
        f"page-{page_number:05d}.json"
    )


class S3JsonWriter:
    """Serialize raw API pages as JSON and upload them to S3."""

    def __init__(
        self,
        *,
        bucket_name: str,
        region_name: str,
        profile_name: str | None = None,
        s3_client: Any | None = None,
    ) -> None:
        if not bucket_name.strip():
            raise ValueError("bucket_name cannot be empty.")

        if not region_name.strip():
            raise ValueError("region_name cannot be empty.")

        self.bucket_name = bucket_name

        if s3_client is None:
            session = boto3.Session(
                profile_name=profile_name,
                region_name=region_name,
            )
            s3_client = session.client("s3")

        self._s3_client = s3_client

    def write_page(
        self,
        *,
        table_name: str,
        load_type: str,
        page_number: int,
        extracted_at: datetime,
        payload: dict[str, Any],
    ) -> str:
        """Upload one complete API response page and return its S3 URI."""

        timestamp = _as_utc(extracted_at)
        key = build_raw_key(
            table_name=table_name,
            load_type=load_type,
            page_number=page_number,
            extracted_at=timestamp,
        )

        document = {
            "metadata": {
                "table_name": table_name,
                "load_type": load_type,
                "page_number": page_number,
                "extracted_at": timestamp.isoformat().replace("+00:00", "Z"),
            },
            "response": payload,
        }

        body = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")

        self._s3_client.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
        )

        return f"s3://{self.bucket_name}/{key}"
