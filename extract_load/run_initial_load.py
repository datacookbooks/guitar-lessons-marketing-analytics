"""Read the complete API history and report row counts without loading it."""

from __future__ import annotations

import requests

from .config import Settings
from .extract import get_table_names, iter_table_pages


def main() -> None:
    settings = Settings.from_env()

    grand_total = 0

    with requests.Session() as session:
        table_names = get_table_names(
            settings.api_base_url,
            session=session,
            timeout=settings.request_timeout_seconds,
        )

        print(f"Found {len(table_names)} source tables.\n")

        for table_name in table_names:
            table_rows = 0
            page_count = 0
            final_cursor = 0

            for page in iter_table_pages(
                settings.api_base_url,
                table_name,
                since=0,
                limit=settings.api_page_size,
                session=session,
                timeout=settings.request_timeout_seconds,
            ):
                page_count += 1
                table_rows += page["count"]
                final_cursor = page["next_since"]

            grand_total += table_rows

            print(
                f"{table_name}: "
                f"{table_rows:,} rows, "
                f"{page_count:,} pages, "
                f"final cursor {final_cursor:,}"
            )

    print(f"\nTotal rows retrieved: {grand_total:,}")


if __name__ == "__main__":
    main()
