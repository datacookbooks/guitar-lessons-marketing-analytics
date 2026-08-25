# Guitar Lessons Marketing Analytics

This repository contains the downstream analytics pipeline for a synthetic
guitar-lessons subscription business.

The pipeline retrieves raw data from a FastAPI service hosted on Railway,
archives the API responses in Amazon S3, loads them into PostgreSQL, and builds
reporting views for churn, retention, customer lifetime value, campaign
performance, and data quality.

## Current phase

The source API, historical extraction, private S3 archive, PostgreSQL staging
contract, secured RDS instance, and reconciled historical staging load are
complete. The current phase implements manual, watermark-driven incremental
extraction through the same replay-safe S3-to-staging path.

## Historical staging load

The default command performs read-only S3 discovery and never opens a
PostgreSQL connection:

```bash
python -m extract_load.run_historical_staging_load
```

Database writes require both `--apply` and the complete expected page-count
inventory. The loader rediscovers and validates that inventory before opening
PostgreSQL:

```bash
python -m extract_load.run_historical_staging_load \
  --apply \
  --expected-page-counts \
  "dim_plan=1,dim_campaign=1,dim_customer=12,fact_subscription_period=16,fact_payment=49,fact_campaign_daily=3,fact_campaign_assignment=91"
```

The selected extraction date, run ID, S3 bucket, AWS profile, and PostgreSQL
connection values come from the ignored local `.env`; private identifiers are
not hardcoded in the entry point.

## Manual incremental load

The incremental command reads exactly one committed PostgreSQL cursor for each
source table, extracts only later API rows, and archives only nonempty pages
under one UTC run ID. It then rediscovers and validates the exact S3 run before
any staging writes are allowed.

Extract and validate a new S3 run without changing staging:

```bash
python -m extract_load.run_incremental_load
```

Extract, validate, load, and immediately verify exact-run replay safety:

```bash
python -m extract_load.run_incremental_load --apply --verify-replay
```

The command prints the extraction date, run ID, and a seven-table page-count
contract. If a database load is interrupted, use those values to rediscover
and resume that same archived run instead of extracting a replacement run:

```bash
python -m extract_load.run_incremental_load \
  --resume-extract-date YYYY-MM-DD \
  --resume-run-id YYYYMMDDTHHMMSSffffffZ \
  --expected-page-counts "dim_plan=0,dim_campaign=0,dim_customer=0,fact_subscription_period=0,fact_payment=0,fact_campaign_daily=0,fact_campaign_assignment=0" \
  --apply --verify-replay
```

Use the actual page counts printed by the original extraction. Zero-page
tables are part of the contract and do not receive empty S3 objects.

## Documentation

- [Architecture](ARCHITECTURE.md)
- [Source and staging data dictionary](docs/data_dictionary.md)
