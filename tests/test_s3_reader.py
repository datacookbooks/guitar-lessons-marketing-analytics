from __future__ import annotations

import json

import pytest

from extract_load.config import HistoricalRunSettings
from extract_load.s3_reader import (
    S3ObjectRef,
    S3ReadError,
    list_historical_run_objects,
    read_staging_page,
)

RUN = HistoricalRunSettings(
    extract_date="2026-08-24",
    run_id="20260824T191020146277Z",
)


def object_key(table_name: str, page_number: int) -> str:
    return (
        f"raw/historical/{table_name}/"
        "extract_date=2026-08-24/"
        "run_id=20260824T191020146277Z/"
        f"page-{page_number:05d}.json"
    )


class FakePaginator:
    def __init__(self, objects_by_prefix: dict[str, list[list[dict]]]):
        self.objects_by_prefix = objects_by_prefix
        self.calls: list[dict] = []

    def paginate(self, **kwargs):
        self.calls.append(kwargs)
        return [
            {"Contents": contents}
            for contents in self.objects_by_prefix.get(kwargs["Prefix"], [])
        ]


class FakeBody:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.closed = False

    def read(self) -> bytes:
        return self.payload

    def close(self) -> None:
        self.closed = True


class FakeS3Client:
    def __init__(self, *, objects_by_prefix=None, object_payloads=None):
        self.paginator = FakePaginator(objects_by_prefix or {})
        self.object_payloads = object_payloads or {}
        self.bodies: list[FakeBody] = []

    def get_paginator(self, operation_name: str) -> FakePaginator:
        assert operation_name == "list_objects_v2"
        return self.paginator

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        assert Bucket == "test-bucket"
        body = FakeBody(self.object_payloads[Key])
        self.bodies.append(body)
        return {"Body": body}


def prefix(table_name: str) -> str:
    return object_key(table_name, 1).removesuffix("page-00001.json")


def test_lists_every_paginator_page_in_table_and_page_order() -> None:
    client = FakeS3Client(
        objects_by_prefix={
            prefix("dim_customer"): [
                [{"Key": object_key("dim_customer", 2), "Size": 20}],
                [{"Key": object_key("dim_customer", 1), "Size": 10}],
            ],
            prefix("fact_payment"): [
                [{"Key": object_key("fact_payment", 1), "Size": 30}]
            ],
        }
    )

    refs = list_historical_run_objects(
        s3_client=client,
        bucket_name="test-bucket",
        historical_run=RUN,
        table_names=("dim_customer", "fact_payment"),
    )

    assert [(ref.table_name, ref.page_number) for ref in refs] == [
        ("dim_customer", 1),
        ("dim_customer", 2),
        ("fact_payment", 1),
    ]
    assert len(client.paginator.calls) == 2


def test_rejects_a_gap_in_page_numbers() -> None:
    client = FakeS3Client(
        objects_by_prefix={
            prefix("dim_customer"): [
                [
                    {"Key": object_key("dim_customer", 1)},
                    {"Key": object_key("dim_customer", 3)},
                ]
            ]
        }
    )

    with pytest.raises(S3ReadError, match="missing or duplicated"):
        list_historical_run_objects(
            s3_client=client,
            bucket_name="test-bucket",
            historical_run=RUN,
            table_names=("dim_customer",),
        )


def test_reads_closes_and_validates_an_s3_page() -> None:
    key = object_key("dim_plan", 1)
    document = {
        "metadata": {
            "table_name": "dim_plan",
            "load_type": "historical",
            "page_number": 1,
            "extracted_at": "2026-08-24T19:10:20.146277Z",
        },
        "response": {
            "table_name": "dim_plan",
            "since": 0,
            "data": [
                {
                    "_raw_row_id": 1,
                    "_generated_for_date": "2025-12-31",
                    "plan_id": "1",
                    "plan_name": "Free",
                    "monthly_price": "0.0",
                    "estimated_monthly_variable_cost": "1.5",
                    "weekly_recorded_lesson_limit": "2.0",
                    "private_sessions_per_month": "0",
                    "is_paid_plan": "False",
                }
            ],
            "count": 1,
            "next_since": 3,
            "has_more": False,
        },
    }
    client = FakeS3Client(object_payloads={key: json.dumps(document).encode("utf-8")})

    page = read_staging_page(
        s3_client=client,
        bucket_name="test-bucket",
        object_ref=S3ObjectRef(
            table_name="dim_plan",
            page_number=1,
            key=key,
        ),
    )

    assert page.table_name == "dim_plan"
    assert page.rows[0]["_raw_row_id"] == "1"
    assert client.bodies[0].closed is True
