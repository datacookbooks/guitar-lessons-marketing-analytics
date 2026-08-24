"""Tests for paginated API extraction."""

from __future__ import annotations

import requests
import pytest

from extract_load.extract import (
    APIResponseError,
    get_table_names,
    iter_table_pages,
)


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = iter(responses)
        self.calls: list[dict] = []

    def get(self, url: str, *, params=None, timeout=None) -> FakeResponse:
        self.calls.append(
            {
                "url": url,
                "params": params,
                "timeout": timeout,
            }
        )
        return next(self.responses)


def test_get_table_names() -> None:
    session = FakeSession(
        [
            FakeResponse(
                {
                    "tables": [
                        {"table_name": "dim_customer"},
                        {"table_name": "fact_payment"},
                    ]
                }
            )
        ]
    )

    names = get_table_names(
        "https://example.test",
        session=session,
    )

    assert names == ["dim_customer", "fact_payment"]


def test_one_page_stops_when_has_more_is_false() -> None:
    session = FakeSession(
        [
            FakeResponse(
                {
                    "data": [{"_raw_row_id": 7}],
                    "count": 1,
                    "next_since": 7,
                    "has_more": False,
                }
            )
        ]
    )

    pages = list(
        iter_table_pages(
            "https://example.test",
            "fact_payment",
            session=session,
        )
    )

    assert len(pages) == 1
    assert session.calls[0]["params"]["since"] == 0


def test_multiple_pages_use_next_since() -> None:
    session = FakeSession(
        [
            FakeResponse(
                {
                    "data": [{"_raw_row_id": 12}],
                    "count": 1,
                    "next_since": 12,
                    "has_more": True,
                }
            ),
            FakeResponse(
                {
                    "data": [{"_raw_row_id": 20}],
                    "count": 1,
                    "next_since": 20,
                    "has_more": False,
                }
            ),
        ]
    )

    pages = list(
        iter_table_pages(
            "https://example.test",
            "fact_payment",
            session=session,
        )
    )

    assert len(pages) == 2
    assert session.calls[0]["params"]["since"] == 0
    assert session.calls[1]["params"]["since"] == 12


def test_stalled_cursor_raises_error() -> None:
    session = FakeSession(
        [
            FakeResponse(
                {
                    "data": [{"_raw_row_id": 1}],
                    "count": 1,
                    "next_since": 0,
                    "has_more": True,
                }
            )
        ]
    )

    with pytest.raises(APIResponseError, match="cursor did not advance"):
        list(
            iter_table_pages(
                "https://example.test",
                "fact_payment",
                session=session,
            )
        )


def test_http_error_raises_api_response_error() -> None:
    session = FakeSession(
        [FakeResponse({}, status_code=500)]
    )

    with pytest.raises(APIResponseError, match="API request failed"):
        get_table_names(
            "https://example.test",
            session=session,
        )
