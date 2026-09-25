# Guitar Lessons Marketing Analytics

This repository contains the downstream analytics pipeline for a synthetic
guitar-lessons subscription business.

The pipeline retrieves raw data from a FastAPI service hosted on Railway,
archives the API responses in Amazon S3, loads them into PostgreSQL, and builds
reporting views for churn, retention, customer lifetime value, campaign
performance, and data quality.

## Current phase

The source API, historical extraction, private S3 archive, PostgreSQL staging
contract, secured RDS instance, historical and incremental staging loads,
cleaned typed analytics layer, metric contracts, and tested reporting views
are complete. Power BI connectivity, Fabric Free anonymous publication, and
the standardized 16-table Import semantic model are also implemented and
validated. The recurring API-to-reporting ELT pipeline is automated through
GitHub Actions. The Power BI publication remains a deliberately reviewed
static Import snapshot rather than an automatically republished report.

## Daily analytics ELT

`.github/workflows/daily-elt.yml` runs the complete pipeline every day at
07:17 UTC, one hour after the source API's 06:17 UTC generation schedule. The
same workflow can be started manually through `workflow_dispatch` for a safe
rerun or operational recovery.

Both triggers call the same canonical entry point:

```bash
python -m extract_load.run_daily_pipeline --apply
```

The runner archives new API pages to S3, loads staging transactionally,
verifies exact-run replay safety, rebuilds analytics, replaces reporting
views, and checks durable source-to-target invariants. A PostgreSQL advisory
lock and GitHub Actions concurrency group prevent overlapping runs.

See [Daily ELT automation](docs/daily_elt_automation.md) for configuration,
security, validation, and recovery details.

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

## Incremental staging load

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

## Staging-to-analytics transformation

The analytics layer contains seven typed, deduplicated dimensions and facts
plus `analytics.data_quality_issue`. The cleaning contract documents guarded
casts, normalization, deterministic delivery selection, late corrections, the
unknown-campaign member, and measurable rejected or superseded values.

Inspect the reviewed SQL plan without opening PostgreSQL:

```bash
python -m extract_load.run_analytics_transform
```

Apply the current staging data and verify deterministic rerun behavior:

```bash
python -m extract_load.run_analytics_transform \
  --apply \
  --verify-rerun
```

The command validates staging, manifest, watermark, and lineage invariants
before analytics writes. It applies the versioned DDL and transformation,
requires nondecreasing model counts and valid source lineage, and fingerprints
a second unchanged-staging run to prove value invariance. The original
September 19 reviewed counts remain versioned under `tests/fixtures` as
regression evidence; they are not production gates for growing daily data.

## Reporting views

The reporting layer contains seven reusable `analytics` helper views and eleven
dashboard-facing `reporting` views. They implement paid-logo movement and
retention, payment recovery, realized contribution, expected 12-month paid
CLV, daily campaign delivery, randomized treatment/holdout outcomes,
incremental campaign impact, and data-quality summaries. The metric contract
documents every grain, eligibility rule, time window, additive component, and
intended downstream aggregation.

Inspect the reviewed SQL plan without opening PostgreSQL:

```bash
python -m extract_load.run_reporting_views
```

Apply the views using the reporting cutoff derived from current analytics
sources:

```bash
python -m extract_load.run_reporting_views \
  --apply \
  --verify-rerun
```

The command applies all 18 views in one transaction, checks durable reporting
relationships before commit, and reapplies unchanged SQL to prove definition
replaceability.

## Documentation

- [Architecture](ARCHITECTURE.md)
- [Source and staging data dictionary](docs/data_dictionary.md)
- [Analytics cleaning contract](docs/analytics_cleaning_contract.md)
- [Metric definitions and semantic-model contract](docs/metric_definitions.md)
- [Implemented Power BI semantic model](docs/power_bi_semantic_model.md)
- [Dashboard platform and publication decision](docs/dashboard_platform_decision.md)
- [Daily ELT automation](docs/daily_elt_automation.md)
