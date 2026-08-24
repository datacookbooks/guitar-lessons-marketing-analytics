# Guitar Lessons Marketing Analytics

This repository contains the downstream analytics pipeline for a synthetic
guitar-lessons subscription business.

The pipeline retrieves raw data from a FastAPI service hosted on Railway,
archives the API responses in Amazon S3, loads them into PostgreSQL, and builds
reporting views for churn, retention, customer lifetime value, campaign
performance, and data quality.

## Current phase

The source API is deployed and operational. The current phase builds and tests
the API extraction process before adding the AWS storage and database layers.

## Documentation

- [Architecture](ARCHITECTURE.md)
