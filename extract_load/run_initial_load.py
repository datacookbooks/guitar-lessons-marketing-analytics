"""Extract the complete API history and save raw response pages to S3."""

from __future__ import annotations

from datetime import datetime, timezone

import requests

from .config import S3Settings, Settings
from .extract import get_table_names, iter_table_pages
from .s3_writer import S3JsonWriter


def run_initial_load(
    *,
    settings: Settings,
    session: requests.Session,
    writer: S3JsonWriter,
    extracted_at: datetime | None = None,
) -> dict[str, dict[str, int]]:
    """Extract every source table and upload every raw page to S3."""

    run_timestamp = extracted_at or datetime.now(timezone.utc)
    table_summaries: dict[str, dict[str, int]] = {}
    grand_total = 0
    total_pages = 0

    table_names = get_table_names(
        settings.api_base_url,
        session=session,
        timeout=settings.request_timeout_seconds,
    )

    print(f"Found {len(table_names)} source tables.")
    print(f"Historical run started at {run_timestamp.isoformat()}\n")

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
            page_number = page_count + 1

            try:
                writer.write_page(
                    table_name=table_name,
                    load_type="historical",
                    page_number=page_number,
                    extracted_at=run_timestamp,
                    payload=page,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to upload {table_name} page {page_number}."
                ) from exc

            page_count = page_number
            table_rows += page["count"]
            final_cursor = page["next_since"]

        table_summaries[table_name] = {
            "rows": table_rows,
            "pages": page_count,
            "final_cursor": final_cursor,
        }

        grand_total += table_rows
        total_pages += page_count

        print(
            f"{table_name}: "
            f"{table_rows:,} rows, "
            f"{page_count:,} pages, "
            f"final cursor {final_cursor:,}"
        )

    print(f"\nTotal rows uploaded: {grand_total:,}")
    print(f"Total S3 objects created: {total_pages:,}")

    return table_summaries


def main() -> None:
    settings = Settings.from_env()
    s3_settings = S3Settings.from_env()

    writer = S3JsonWriter(
        bucket_name=s3_settings.bucket_name,
        region_name=s3_settings.region,
        profile_name=s3_settings.profile,
    )

    with requests.Session() as session:
        run_initial_load(
            settings=settings,
            session=session,
            writer=writer,
        )


if __name__ == "__main__":
    main()
