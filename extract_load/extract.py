"""Read paginated raw data from the Railway API."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import requests


class APIResponseError(RuntimeError):
    """Raised when the source API returns an invalid or unsuccessful response."""


def _get_json(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    try:
        response = session.get(url, params=params, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise APIResponseError(f"API request failed for {url}: {exc}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise APIResponseError(f"API returned invalid JSON for {url}.") from exc

    if not isinstance(payload, dict):
        raise APIResponseError(f"API response for {url} was not a JSON object.")

    return payload


def get_table_names(
    base_url: str,
    *,
    session: requests.Session,
    timeout: int = 60,
) -> list[str]:
    """Return the source-table names advertised by the API."""

    payload = _get_json(
        session,
        f"{base_url}/tables",
        timeout=timeout,
    )

    table_items = payload.get("tables")

    if not isinstance(table_items, list):
        raise APIResponseError("The /tables response did not contain a table list.")

    table_names: list[str] = []

    for item in table_items:
        if not isinstance(item, dict):
            raise APIResponseError("The /tables response contained an invalid item.")

        table_name = item.get("table_name")

        if not isinstance(table_name, str) or not table_name:
            raise APIResponseError("A table entry did not contain a valid table_name.")

        table_names.append(table_name)

    if len(table_names) != len(set(table_names)):
        raise APIResponseError("The /tables response contained duplicate table names.")

    return table_names


def iter_table_pages(
    base_url: str,
    table_name: str,
    *,
    since: int = 0,
    limit: int = 1_000,
    session: requests.Session,
    timeout: int = 60,
) -> Iterator[dict[str, Any]]:
    """Yield every API page after a table's starting cursor."""

    cursor = since

    while True:
        payload = _get_json(
            session,
            f"{base_url}/tables/{table_name}/raw",
            params={
                "since": cursor,
                "limit": limit,
            },
            timeout=timeout,
        )

        data = payload.get("data")
        next_since = payload.get("next_since")
        has_more = payload.get("has_more")
        count = payload.get("count")

        if not isinstance(data, list):
            raise APIResponseError(f"{table_name}: data was not a list.")

        if (
            not isinstance(next_since, int)
            or isinstance(next_since, bool)
            or next_since < 0
        ):
            raise APIResponseError(f"{table_name}: next_since was invalid.")

        if not isinstance(has_more, bool):
            raise APIResponseError(f"{table_name}: has_more was not Boolean.")

        if count != len(data):
            raise APIResponseError(
                f"{table_name}: count did not match the number of returned rows."
            )

        if data and next_since <= cursor:
            raise APIResponseError(
                f"{table_name}: the cursor did not advance beyond {cursor}."
            )

        if has_more and not data:
            raise APIResponseError(
                f"{table_name}: the API reported more data but returned no rows."
            )

        yield payload

        if not has_more:
            break

        cursor = next_since
