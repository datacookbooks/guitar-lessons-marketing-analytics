# Guitar Lessons Marketing Analytics

This repository contains the downstream analytics pipeline for a synthetic
guitar-lessons subscription business.

The pipeline retrieves raw data from a FastAPI service hosted on Railway,
archives the API responses in Amazon S3, loads them into PostgreSQL, and builds
reporting views for churn, retention, customer lifetime value, campaign
performance, and data quality.

## Current phase

The source API, historical extraction, and private S3 raw archive are complete.
The PostgreSQL staging contract is versioned and tested. The current phase
builds the S3-to-staging loader and its transaction, replay, and watermark
protections before provisioning RDS.

## Documentation

- [Architecture](ARCHITECTURE.md)
- [Source and staging data dictionary](docs/data_dictionary.md)
