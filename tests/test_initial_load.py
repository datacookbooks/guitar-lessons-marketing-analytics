from datetime import datetime, timezone
from unittest.mock import Mock

import extract_load.run_initial_load as initial_load
from extract_load.config import Settings


def test_initial_load_writes_every_page(monkeypatch) -> None:
    extracted_at = datetime(
        2026,
        8,
        24,
        19,
        0,
        tzinfo=timezone.utc,
    )
    pages = [
        {
            "data": [{"customer_id": 1}],
            "next_since": 1,
            "has_more": True,
            "count": 1,
        },
        {
            "data": [{"customer_id": 2}],
            "next_since": 2,
            "has_more": False,
            "count": 1,
        },
    ]

    get_table_names = Mock(return_value=["customers"])
    iter_table_pages = Mock(return_value=iter(pages))

    monkeypatch.setattr(initial_load, "get_table_names", get_table_names)
    monkeypatch.setattr(initial_load, "iter_table_pages", iter_table_pages)

    writer = Mock()
    session = Mock()
    settings = Settings(api_base_url="https://example.test")

    summary = initial_load.run_initial_load(
        settings=settings,
        session=session,
        writer=writer,
        extracted_at=extracted_at,
    )

    assert writer.write_page.call_count == 2

    first_upload = writer.write_page.call_args_list[0].kwargs
    second_upload = writer.write_page.call_args_list[1].kwargs

    assert first_upload["table_name"] == "customers"
    assert first_upload["load_type"] == "historical"
    assert first_upload["page_number"] == 1
    assert first_upload["extracted_at"] == extracted_at
    assert first_upload["payload"] == pages[0]

    assert second_upload["page_number"] == 2
    assert second_upload["payload"] == pages[1]

    assert summary == {
        "customers": {
            "rows": 2,
            "pages": 2,
            "final_cursor": 2,
        }
    }
