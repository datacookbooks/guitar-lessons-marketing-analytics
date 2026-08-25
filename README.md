# Guitar Lessons Marketing Analytics

This repository contains the downstream analytics pipeline for a synthetic
guitar-lessons subscription business.

The pipeline retrieves raw data from a FastAPI service hosted on Railway,
archives the API responses in Amazon S3, loads them into PostgreSQL, and builds
reporting views for churn, retention, customer lifetime value, campaign
performance, and data quality.

## Current phase

The source API, historical extraction, and private S3 raw archive are complete.
The PostgreSQL staging contract and transactional, replay-safe S3 loader are
versioned and tested. A secured RDS PostgreSQL instance and opt-in live smoke
test are also complete. The current phase loads and reconciles the explicitly
selected historical S3 delivery before analytics transformations begin.

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

## Documentation

- [Architecture](ARCHITECTURE.md)
- [Source and staging data dictionary](docs/data_dictionary.md)
