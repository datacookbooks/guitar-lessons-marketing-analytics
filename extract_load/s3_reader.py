"""Discover and read one explicitly selected historical S3 delivery."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import boto3

from .config import HistoricalRunSettings, S3Settings
from .staging_rows import SOURCE_COLUMNS, StagingPage, s3_document_to_staging_page

_PAGE_KEY = re.compile(
    r"^raw/historical/(?P<table_name>[a-z][a-z0-9_]*)/"
    r"extract_date=(?P<extract_date>\d{4}-\d{2}-\d{2})/"
    r"run_id=(?P<run_id>\d{8}T\d{12}Z)/"
    r"page-(?P<page_number>\d{5})\.json$"
)


class S3ReadError(RuntimeError):
    """Raised when the selected historical S3 delivery cannot be read safely."""


@dataclass(frozen=True)
class S3ObjectRef:
    """Validated location and list metadata for one raw S3 page."""

    table_name: str
    page_number: int
    key: str
    size_bytes: int | None = None
    etag: str | None = None


def create_s3_client(settings: S3Settings) -> Any:
    """Create the configured read client without exposing account details."""

    session = boto3.Session(
        profile_name=settings.profile,
        region_name=settings.region,
    )
    return session.client("s3")


def _validate_table_names(table_names: Iterable[str]) -> tuple[str, ...]:
    names = tuple(table_names)
    if not names:
        raise ValueError("At least one source table is required.")
    if len(names) != len(set(names)):
        raise ValueError("Source table names must be unique.")

    unsupported = [name for name in names if name not in SOURCE_COLUMNS]
    if unsupported:
        raise ValueError(f"Unsupported source tables: {sorted(unsupported)}")
    return names


def list_historical_run_objects(
    *,
    s3_client: Any,
    bucket_name: str,
    historical_run: HistoricalRunSettings,
    table_names: Iterable[str] = SOURCE_COLUMNS,
) -> tuple[S3ObjectRef, ...]:
    """List a complete selected run and return objects in table/page order."""

    if not bucket_name.strip():
        raise ValueError("bucket_name cannot be empty.")

    names = _validate_table_names(table_names)
    refs: list[S3ObjectRef] = []

    try:
        paginator = s3_client.get_paginator("list_objects_v2")

        for table_name in names:
            prefix = (
                f"raw/historical/{table_name}/"
                f"extract_date={historical_run.extract_date}/"
                f"run_id={historical_run.run_id}/"
            )
            table_refs: list[S3ObjectRef] = []

            for listing_page in paginator.paginate(
                Bucket=bucket_name,
                Prefix=prefix,
            ):
                contents = listing_page.get("Contents", [])
                if not isinstance(contents, list):
                    raise S3ReadError(f"S3 returned invalid Contents for {table_name}.")

                for item in contents:
                    if not isinstance(item, dict):
                        raise S3ReadError(
                            f"S3 returned an invalid object entry for {table_name}."
                        )

                    key = item.get("Key")
                    if not isinstance(key, str):
                        raise S3ReadError(
                            f"S3 returned an object without a valid key for {table_name}."
                        )

                    match = _PAGE_KEY.fullmatch(key)
                    if match is None:
                        raise S3ReadError(
                            f"Unexpected object under the selected prefix: {key}"
                        )

                    key_values = match.groupdict()
                    if (
                        key_values["table_name"] != table_name
                        or key_values["extract_date"] != historical_run.extract_date
                        or key_values["run_id"] != historical_run.run_id
                    ):
                        raise S3ReadError(
                            f"Object key did not match the selected run: {key}"
                        )

                    page_number = int(key_values["page_number"])
                    if page_number < 1:
                        raise S3ReadError(f"Invalid page number in S3 key: {key}")

                    size = item.get("Size")
                    etag = item.get("ETag")
                    table_refs.append(
                        S3ObjectRef(
                            table_name=table_name,
                            page_number=page_number,
                            key=key,
                            size_bytes=(
                                size
                                if isinstance(size, int) and not isinstance(size, bool)
                                else None
                            ),
                            etag=etag if isinstance(etag, str) else None,
                        )
                    )

            if not table_refs:
                raise S3ReadError(
                    f"No objects found for {table_name} in the selected run."
                )

            table_refs.sort(key=lambda ref: ref.page_number)
            actual_pages = [ref.page_number for ref in table_refs]
            expected_pages = list(range(1, len(table_refs) + 1))
            if actual_pages != expected_pages:
                raise S3ReadError(
                    f"{table_name} pages were missing or duplicated; "
                    f"found {actual_pages}."
                )

            refs.extend(table_refs)
    except S3ReadError:
        raise
    except Exception as exc:
        raise S3ReadError("Failed to list the selected historical S3 run.") from exc

    return tuple(refs)


def read_s3_json_object(
    *,
    s3_client: Any,
    bucket_name: str,
    object_ref: S3ObjectRef,
) -> dict[str, Any]:
    """Read and decode one object without changing S3."""

    body: Any | None = None
    try:
        response = s3_client.get_object(
            Bucket=bucket_name,
            Key=object_ref.key,
        )
        body = response.get("Body")
        if body is None or not callable(getattr(body, "read", None)):
            raise S3ReadError(f"S3 object had no readable body: {object_ref.key}")

        payload = json.loads(body.read())
    except S3ReadError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise S3ReadError(f"S3 object was not valid JSON: {object_ref.key}") from exc
    except Exception as exc:
        raise S3ReadError(f"Failed to read S3 object: {object_ref.key}") from exc
    finally:
        if body is not None and callable(getattr(body, "close", None)):
            body.close()

    if not isinstance(payload, dict):
        raise S3ReadError(f"S3 object was not a JSON object: {object_ref.key}")
    return payload


def read_staging_page(
    *,
    s3_client: Any,
    bucket_name: str,
    object_ref: S3ObjectRef,
) -> StagingPage:
    """Read one S3 object and validate it against the staging contract."""

    document = read_s3_json_object(
        s3_client=s3_client,
        bucket_name=bucket_name,
        object_ref=object_ref,
    )
    return s3_document_to_staging_page(
        document,
        source_s3_key=object_ref.key,
    )
