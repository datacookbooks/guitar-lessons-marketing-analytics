from datetime import datetime, timezone

import pytest

from extract_load.staging_rows import (
    SOURCE_COLUMNS,
    StagingPageError,
    s3_document_to_staging_page,
)

EXTRACTED_AT = "2026-08-24T19:10:20.146277Z"
SOURCE_KEY = (
    "raw/historical/dim_customer/"
    "extract_date=2026-08-24/"
    "run_id=20260824T191020146277Z/"
    "page-00001.json"
)
LOADED_AT = datetime(2026, 8, 24, 20, 0, tzinfo=timezone.utc)


def customer_row() -> dict:
    return {
        "_raw_row_id": 7,
        "_generated_for_date": "2025-12-31",
        "customer_id": "98317178786614",
        "prospect_key": "O-20250606-0004",
        "email": "",
        "signup_timestamp": "2025-06-06 08:11:00+00:00",
        "state": "GA",
        "experience_level": " Beginner ",
        "initial_acquisition_channel": "ORGANIC",
        "first_campaign_id": None,
        "source_updated_at": "2025-06-06 10:11:00+00:00",
    }


def customer_document() -> dict:
    return {
        "metadata": {
            "table_name": "dim_customer",
            "load_type": "historical",
            "page_number": 1,
            "extracted_at": EXTRACTED_AT,
        },
        "response": {
            "table_name": "dim_customer",
            "since": 0,
            "data": [customer_row()],
            "count": 1,
            "next_since": 1006,
            "has_more": True,
        },
    }


def test_converts_s3_document_to_staging_parameters() -> None:
    page = s3_document_to_staging_page(
        customer_document(),
        source_s3_key=SOURCE_KEY,
        loaded_at=LOADED_AT,
    )

    assert page.table_name == "dim_customer"
    assert page.source_cursor_start == 0
    assert page.source_cursor_end == 1006
    assert page.source_run_id == "20260824T191020146277Z"
    assert page.row_count == 1
    assert page.insert_columns == SOURCE_COLUMNS["dim_customer"] + (
        "source_s3_key",
        "source_run_id",
        "source_load_type",
        "source_page_number",
        "source_cursor_start",
        "source_cursor_end",
        "extracted_at",
        "loaded_at",
    )

    row = page.rows[0]
    assert row["_raw_row_id"] == "7"
    assert row["email"] == ""
    assert row["experience_level"] == " Beginner "
    assert row["first_campaign_id"] is None
    assert row["source_s3_key"] == SOURCE_KEY
    assert row["source_cursor_end"] == 1006
    assert row["loaded_at"] == LOADED_AT


def test_raw_row_id_is_not_used_as_the_api_cursor() -> None:
    document = customer_document()
    document["response"]["data"][0]["_raw_row_id"] = "55"
    document["response"]["next_since"] = 900

    page = s3_document_to_staging_page(
        document,
        source_s3_key=SOURCE_KEY,
        loaded_at=LOADED_AT,
    )

    assert page.rows[0]["_raw_row_id"] == "55"
    assert page.source_cursor_end == 900


def test_rejects_business_field_json_type_drift() -> None:
    document = customer_document()
    document["response"]["data"][0]["customer_id"] = 98317178786614

    with pytest.raises(StagingPageError, match="customer_id changed JSON type"):
        s3_document_to_staging_page(
            document,
            source_s3_key=SOURCE_KEY,
            loaded_at=LOADED_AT,
        )


def test_rejects_boolean_raw_row_id() -> None:
    document = customer_document()
    document["response"]["data"][0]["_raw_row_id"] = True

    with pytest.raises(StagingPageError, match="_raw_row_id"):
        s3_document_to_staging_page(
            document,
            source_s3_key=SOURCE_KEY,
            loaded_at=LOADED_AT,
        )


def test_rejects_source_column_drift() -> None:
    document = customer_document()
    del document["response"]["data"][0]["email"]
    document["response"]["data"][0]["new_source_field"] = "unexpected"

    with pytest.raises(StagingPageError, match="source columns"):
        s3_document_to_staging_page(
            document,
            source_s3_key=SOURCE_KEY,
            loaded_at=LOADED_AT,
        )


def test_rejects_wrapper_table_mismatch() -> None:
    document = customer_document()
    document["response"]["table_name"] = "fact_payment"

    with pytest.raises(StagingPageError, match="response.table_name"):
        s3_document_to_staging_page(
            document,
            source_s3_key=SOURCE_KEY,
            loaded_at=LOADED_AT,
        )


def test_rejects_count_mismatch() -> None:
    document = customer_document()
    document["response"]["count"] = 2

    with pytest.raises(StagingPageError, match="count"):
        s3_document_to_staging_page(
            document,
            source_s3_key=SOURCE_KEY,
            loaded_at=LOADED_AT,
        )


def test_rejects_s3_key_that_disagrees_with_metadata() -> None:
    wrong_key = SOURCE_KEY.replace("dim_customer", "fact_payment")

    with pytest.raises(StagingPageError, match="S3 key table_name"):
        s3_document_to_staging_page(
            customer_document(),
            source_s3_key=wrong_key,
            loaded_at=LOADED_AT,
        )
