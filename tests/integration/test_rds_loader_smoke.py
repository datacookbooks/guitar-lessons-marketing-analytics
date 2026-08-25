"""Opt-in smoke test for the real S3-to-RDS staging path."""

import os
from pathlib import Path

import boto3
import psycopg
import pytest
from dotenv import load_dotenv

from extract_load.postgres_loader import load_staging_page
from extract_load.s3_reader import S3ObjectRef, read_staging_page


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_RDS_INTEGRATION") != "1",
    reason="Set RUN_RDS_INTEGRATION=1 to run the live AWS/RDS smoke test.",
)

REQUIRED_ENV = (
    "AWS_PROFILE",
    "AWS_REGION",
    "S3_BUCKET_NAME",
    "S3_HISTORICAL_EXTRACT_DATE",
    "S3_HISTORICAL_RUN_ID",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_SSLMODE",
    "POSTGRES_SSLROOTCERT",
    "POSTGRES_CONNECT_TIMEOUT",
)


def _environment() -> dict[str, str]:
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        pytest.fail(f"Missing required environment variables: {', '.join(missing)}")
    return {name: os.environ[name] for name in REQUIRED_ENV}


def _connect(env: dict[str, str]):
    return psycopg.connect(
        host=env["POSTGRES_HOST"],
        port=int(env["POSTGRES_PORT"]),
        dbname=env["POSTGRES_DB"],
        user=env["POSTGRES_USER"],
        password=env["POSTGRES_PASSWORD"],
        sslmode=env["POSTGRES_SSLMODE"],
        sslrootcert=env["POSTGRES_SSLROOTCERT"],
        connect_timeout=int(env["POSTGRES_CONNECT_TIMEOUT"]),
    )


def test_dim_plan_page_loads_once_and_replays_safely():
    env = _environment()
    table_name = "dim_plan"
    object_key = (
        f"raw/historical/{table_name}/"
        f"extract_date={env['S3_HISTORICAL_EXTRACT_DATE']}/"
        f"run_id={env['S3_HISTORICAL_RUN_ID']}/"
        "page-00001.json"
    )

    s3_client = boto3.Session(
        profile_name=env["AWS_PROFILE"],
        region_name=env["AWS_REGION"],
    ).client("s3")

    page = read_staging_page(
        s3_client=s3_client,
        bucket_name=env["S3_BUCKET_NAME"],
        object_ref=S3ObjectRef(
            table_name=table_name,
            page_number=1,
            key=object_key,
        ),
    )

    with _connect(env) as connection:
        first_result = load_staging_page(connection, page)

    with _connect(env) as connection:
        replay_result = load_staging_page(connection, page)

    with _connect(env) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*), COUNT(DISTINCT _raw_row_id)
                FROM staging.dim_plan
                """
            )
            total_rows, distinct_raw_ids = cursor.fetchone()

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM staging.etl_loaded_object
                WHERE source_s3_key = %s
                """,
                (object_key,),
            )
            loaded_object_count = cursor.fetchone()[0]

    assert first_result.status in {"loaded", "skipped"}
    assert replay_result.status == "skipped"
    assert total_rows == first_result.source_row_count
    assert distinct_raw_ids == total_rows
    assert loaded_object_count == 1
