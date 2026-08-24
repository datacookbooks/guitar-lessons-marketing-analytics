import json
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from extract_load.s3_writer import S3JsonWriter, build_raw_key


EXTRACTED_AT = datetime(
    2026,
    8,
    24,
    18,
    30,
    0,
    123456,
    tzinfo=timezone.utc,
)


def test_build_raw_key() -> None:
    key = build_raw_key(
        table_name="customers",
        load_type="historical",
        page_number=3,
        extracted_at=EXTRACTED_AT,
    )

    assert key == (
        "raw/historical/customers/"
        "extract_date=2026-08-24/"
        "run_id=20260824T183000123456Z/"
        "page-00003.json"
    )


def test_write_page_uploads_json() -> None:
    client = Mock()
    writer = S3JsonWriter(
        bucket_name="test-bucket",
        region_name="us-east-1",
        s3_client=client,
    )
    payload = {
        "rows": [{"customer_id": 1}],
        "has_more": False,
    }

    uri = writer.write_page(
        table_name="customers",
        load_type="historical",
        page_number=1,
        extracted_at=EXTRACTED_AT,
        payload=payload,
    )

    client.put_object.assert_called_once()
    request = client.put_object.call_args.kwargs

    assert request["Bucket"] == "test-bucket"
    assert request["ContentType"] == "application/json"
    assert request["ServerSideEncryption"] == "AES256"

    uploaded_document = json.loads(request["Body"])

    assert uploaded_document["response"] == payload
    assert uploaded_document["metadata"]["table_name"] == "customers"
    assert uploaded_document["metadata"]["load_type"] == "historical"
    assert uploaded_document["metadata"]["page_number"] == 1
    assert uri.startswith("s3://test-bucket/raw/historical/customers/")


def test_build_raw_key_rejects_invalid_page_number() -> None:
    with pytest.raises(ValueError, match="page_number"):
        build_raw_key(
            table_name="customers",
            load_type="historical",
            page_number=0,
            extracted_at=EXTRACTED_AT,
        )
