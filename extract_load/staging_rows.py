"""Validate an S3 raw page and convert its rows to staging parameters."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

SOURCE_COLUMNS: dict[str, tuple[str, ...]] = {
    "dim_plan": (
        "_raw_row_id",
        "_generated_for_date",
        "plan_id",
        "plan_name",
        "monthly_price",
        "estimated_monthly_variable_cost",
        "weekly_recorded_lesson_limit",
        "private_sessions_per_month",
        "is_paid_plan",
    ),
    "dim_campaign": (
        "_raw_row_id",
        "_generated_for_date",
        "campaign_id",
        "campaign_name",
        "channel",
        "objective",
        "primary_conversion_event",
        "active_start_date",
        "active_end_date",
        "default_treatment_share",
        "is_evergreen",
    ),
    "dim_customer": (
        "_raw_row_id",
        "_generated_for_date",
        "customer_id",
        "prospect_key",
        "email",
        "signup_timestamp",
        "state",
        "experience_level",
        "initial_acquisition_channel",
        "first_campaign_id",
        "source_updated_at",
    ),
    "fact_subscription_period": (
        "_raw_row_id",
        "_generated_for_date",
        "subscription_period_id",
        "customer_id",
        "plan_id",
        "period_start_timestamp",
        "period_end_timestamp",
        "end_reason",
        "source_updated_at",
    ),
    "fact_payment": (
        "_raw_row_id",
        "_generated_for_date",
        "payment_id",
        "subscription_period_id",
        "customer_id",
        "payment_timestamp",
        "amount",
        "payment_type",
        "payment_status",
        "attempt_number",
        "ingested_at",
    ),
    "fact_campaign_daily": (
        "_raw_row_id",
        "_generated_for_date",
        "metric_date",
        "campaign_id",
        "impressions",
        "clicks",
        "spend",
        "platform_attributed_conversions",
        "ingested_at",
    ),
    "fact_campaign_assignment": (
        "_raw_row_id",
        "_generated_for_date",
        "assignment_id",
        "campaign_id",
        "measurement_window_id",
        "prospect_key",
        "customer_id",
        "assignment_arm",
        "assigned_at",
        "first_exposed_at",
        "source_updated_at",
    ),
}

OPTIONAL_SOURCE_COLUMNS: dict[str, frozenset[str]] = {
    # Historical raw objects predate this additive campaign field.
    "dim_campaign": frozenset({"primary_conversion_event"}),
}

LINEAGE_COLUMNS = (
    "source_s3_key",
    "source_run_id",
    "source_load_type",
    "source_page_number",
    "source_cursor_start",
    "source_cursor_end",
    "extracted_at",
    "loaded_at",
)

_S3_KEY = re.compile(
    r"^raw/(?P<load_type>[a-z][a-z0-9_]*)/"
    r"(?P<table_name>[a-z][a-z0-9_]*)/"
    r"extract_date=(?P<extract_date>\d{4}-\d{2}-\d{2})/"
    r"run_id=(?P<run_id>\d{8}T\d{12}Z)/"
    r"page-(?P<page_number>\d{5})\.json$"
)


class StagingPageError(ValueError):
    """Raised when an S3 raw document does not satisfy the staging contract."""


@dataclass(frozen=True)
class StagingPage:
    """Validated page metadata and parameters for a future database loader."""

    table_name: str
    source_s3_key: str
    source_run_id: str
    source_load_type: str
    source_page_number: int
    source_cursor_start: int
    source_cursor_end: int
    source_has_more: bool
    extracted_at: datetime
    loaded_at: datetime
    rows: tuple[dict[str, Any], ...]

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def insert_columns(self) -> tuple[str, ...]:
        return SOURCE_COLUMNS[self.table_name] + LINEAGE_COLUMNS


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StagingPageError(f"{name} must be a JSON object.")
    return value


def _require_integer(value: Any, name: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise StagingPageError(f"{name} must be an integer of at least {minimum}.")
    return value


def _parse_timestamp(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise StagingPageError(f"{name} must be a non-empty timestamp string.")

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StagingPageError(f"{name} was not a valid ISO timestamp.") from exc

    if parsed.tzinfo is None:
        raise StagingPageError(f"{name} must include a timezone.")

    return parsed.astimezone(timezone.utc)


def _as_raw_row_id_text(value: Any) -> str:
    if isinstance(value, bool):
        raise StagingPageError(
            "_raw_row_id must be a nonnegative integer or digit-only string."
        )
    if isinstance(value, int) and value >= 0:
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return value
    raise StagingPageError(
        "_raw_row_id must be a nonnegative integer or digit-only string."
    )


def _as_business_text(value: Any, column_name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise StagingPageError(
        f"{column_name} changed JSON type; expected a string or null, "
        f"received {type(value).__name__}."
    )


def s3_document_to_staging_page(
    document: dict[str, Any],
    *,
    source_s3_key: str,
    loaded_at: datetime | None = None,
) -> StagingPage:
    """Validate one implemented S3 wrapper and return staging-row parameters."""

    key_match = _S3_KEY.fullmatch(source_s3_key)
    if key_match is None:
        raise StagingPageError(
            "source_s3_key did not match the implemented raw key format."
        )

    metadata = _require_mapping(document.get("metadata"), "metadata")
    response = _require_mapping(document.get("response"), "response")

    table_name = metadata.get("table_name")
    load_type = metadata.get("load_type")
    page_number = _require_integer(
        metadata.get("page_number"),
        "metadata.page_number",
        minimum=1,
    )
    extracted_at = _parse_timestamp(
        metadata.get("extracted_at"),
        "metadata.extracted_at",
    )

    if table_name not in SOURCE_COLUMNS:
        raise StagingPageError(f"Unsupported table_name: {table_name!r}.")
    if not isinstance(load_type, str) or not load_type:
        raise StagingPageError("metadata.load_type must be a non-empty string.")

    key_values = key_match.groupdict()
    expected_run_id = extracted_at.strftime("%Y%m%dT%H%M%S%fZ")
    expected_extract_date = extracted_at.strftime("%Y-%m-%d")

    if key_values["table_name"] != table_name:
        raise StagingPageError("S3 key table_name did not match metadata.table_name.")
    if key_values["load_type"] != load_type:
        raise StagingPageError("S3 key load_type did not match metadata.load_type.")
    if int(key_values["page_number"]) != page_number:
        raise StagingPageError("S3 key page number did not match metadata.page_number.")
    if key_values["run_id"] != expected_run_id:
        raise StagingPageError("S3 key run_id did not match metadata.extracted_at.")
    if key_values["extract_date"] != expected_extract_date:
        raise StagingPageError(
            "S3 key extract_date did not match metadata.extracted_at."
        )

    response_table_name = response.get("table_name")
    if response_table_name != table_name:
        raise StagingPageError("response.table_name did not match metadata.table_name.")

    cursor_start = _require_integer(response.get("since"), "response.since")
    cursor_end = _require_integer(response.get("next_since"), "response.next_since")
    count = _require_integer(response.get("count"), "response.count")
    has_more = response.get("has_more")
    rows = response.get("data")

    if not isinstance(has_more, bool):
        raise StagingPageError("response.has_more must be Boolean.")
    if not isinstance(rows, list):
        raise StagingPageError("response.data must be a list.")
    if count != len(rows):
        raise StagingPageError("response.count did not match the number of rows.")
    if cursor_end < cursor_start:
        raise StagingPageError("response.next_since cannot precede response.since.")
    if rows and cursor_end <= cursor_start:
        raise StagingPageError("A non-empty page must advance the API cursor.")
    if has_more and not rows:
        raise StagingPageError("response.has_more cannot be true for an empty page.")

    load_timestamp = loaded_at or datetime.now(timezone.utc)
    if load_timestamp.tzinfo is None:
        raise StagingPageError("loaded_at must include a timezone.")
    load_timestamp = load_timestamp.astimezone(timezone.utc)

    expected_columns = SOURCE_COLUMNS[table_name]
    expected_column_set = set(expected_columns)
    converted_rows: list[dict[str, Any]] = []

    for row_number, raw_row in enumerate(rows, start=1):
        row = _require_mapping(raw_row, f"response.data[{row_number - 1}]")
        actual_columns = set(row)

        if actual_columns != expected_column_set:
            optional_columns = OPTIONAL_SOURCE_COLUMNS.get(
                table_name,
                frozenset(),
            )
            missing = sorted(
                expected_column_set - actual_columns - optional_columns
            )
            unexpected = sorted(actual_columns - expected_column_set)

            if missing or unexpected:
                raise StagingPageError(
                    f"Row {row_number} did not match the "
                    f"{table_name} source columns; "
                    f"missing={missing}, unexpected={unexpected}."
                )

        converted = {
            column: (
                _as_raw_row_id_text(row[column])
                if column == "_raw_row_id"
                else _as_business_text(row.get(column), column)
            )
            for column in expected_columns
        }

        converted.update(
            {
                "source_s3_key": source_s3_key,
                "source_run_id": key_values["run_id"],
                "source_load_type": load_type,
                "source_page_number": page_number,
                "source_cursor_start": cursor_start,
                "source_cursor_end": cursor_end,
                "extracted_at": extracted_at,
                "loaded_at": load_timestamp,
            }
        )
        converted_rows.append(converted)

    return StagingPage(
        table_name=table_name,
        source_s3_key=source_s3_key,
        source_run_id=key_values["run_id"],
        source_load_type=load_type,
        source_page_number=page_number,
        source_cursor_start=cursor_start,
        source_cursor_end=cursor_end,
        source_has_more=has_more,
        extracted_at=extracted_at,
        loaded_at=load_timestamp,
        rows=tuple(converted_rows),
    )
