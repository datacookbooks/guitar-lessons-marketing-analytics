# Daily analytics ELT automation

## Purpose

The analytics repository runs one canonical API-to-reporting pipeline every
day. The source API generates its daily data at 06:17 UTC. This repository's
workflow starts at 07:17 UTC, leaving a one-hour buffer without coupling the
two repositories.

GitHub Actions schedules can start later than the specified minute during
periods of high demand. This does not compromise correctness: extraction is
watermark-driven, missed source dates are backfilled upstream, and reruns are
idempotent.

## Triggers

The workflow has two triggers:

- `schedule` runs the production refresh daily.
- `workflow_dispatch` starts the same production refresh manually for recovery
  or verification.

There is no separate manual implementation. Both triggers execute:

```bash
python -m extract_load.run_daily_pipeline --apply
```

Running the module without `--apply` performs read-only API and PostgreSQL
readiness checks. It does not create S3 objects or change PostgreSQL rows.

## Daily execution order

One successful run:

1. runs the unit-test suite;
2. assumes a short-lived AWS role through GitHub OpenID Connect (OIDC);
3. retrieves one RDS connection secret from AWS Secrets Manager;
4. downloads the official Amazon RDS CA bundle;
5. temporarily authorizes the GitHub-hosted runner's public IPv4 address;
6. acquires a PostgreSQL advisory lock;
7. reads the seven committed API watermarks;
8. extracts and archives every later nonempty API page in S3;
9. rediscovers and validates the exact S3 run;
10. loads each page transactionally into staging;
11. replays the exact run and requires every object to skip;
12. rebuilds and validates the analytics layer;
13. replaces and validates the reporting views;
14. writes a sanitized GitHub Actions summary; and
15. removes the temporary security-group authorization, including after a
    failed pipeline step.

The workflow uses a GitHub concurrency group with
`cancel-in-progress: false`. The PostgreSQL advisory lock also protects
against overlap with a locally launched process.

## Repository configuration

Configure these GitHub Actions variables:

| Variable | Purpose |
| --- | --- |
| `AWS_REGION` | AWS Region containing S3, RDS, and the secret |
| `API_BASE_URL` | Public base URL of the source API |
| `API_PAGE_SIZE` | Optional page size; defaults to `1000` |

Configure these GitHub Actions secrets:

| Secret | Purpose |
| --- | --- |
| `AWS_ROLE_ARN` | Least-privilege role assumed through GitHub OIDC |
| `S3_BUCKET_NAME` | Private raw-data archive bucket |
| `RDS_SECRET_ID` | Identifier of the Secrets Manager RDS secret |
| `RDS_SECURITY_GROUP_ID` | Security group temporarily updated for the runner |

The RDS secret must be a JSON object with these standard fields:

```json
{
  "host": "database endpoint",
  "port": 5432,
  "dbname": "database name",
  "username": "pipeline role",
  "password": "database password"
}
```

The exporter also accepts `database` in place of `dbname` and `user` in place
of `username`. Secret values are masked before they are exported to later
workflow steps.

## AWS permissions

The OIDC trust policy should be restricted to this repository and its default
branch. The assumed role needs only:

- read and write access to the required raw S3 prefixes;
- `secretsmanager:GetSecretValue` for the one RDS secret;
- `ec2:AuthorizeSecurityGroupIngress` and
  `ec2:RevokeSecurityGroupIngress` for the selected security group; and
- the basic identity call used when AWS credentials are configured.

Do not create permanent AWS access keys for this workflow. Do not grant broad
administrator permissions, open PostgreSQL to `0.0.0.0/0`, or disable TLS
certificate or hostname verification.

## Recurring validation contract

Daily production checks are based on properties that remain correct as data
grows:

- exactly seven supported source tables and watermarks exist;
- every staging raw-row identifier is unique;
- every staging row points to a loaded-object manifest;
- manifest row totals cover the distinct rows retained in staging;
- each watermark matches its greatest committed manifest cursor;
- the exact S3 run matches its extraction page and cursor contract;
- replaying the exact run loads zero pages;
- analytics row counts do not decrease under the append-only transformation;
- every analytics and quality row retains valid staging lineage;
- the synthetic unknown campaign remains valid;
- retained customers do not exceed eligible customers;
- recovered billing episodes do not exceed failed episodes;
- final 90-day experiment windows do not exceed all windows;
- reporting quality counts equal analytics quality counts; and
- unchanged inputs produce unchanged analytics values and reporting view
  definitions.

The exact counts from the reviewed September 19 milestone are preserved in
`tests/fixtures/reviewed_baseline_2026_09_19.json`. That file is historical
regression evidence, not a daily production gate.

## Failure and recovery

The workflow fails closed: a downstream stage does not run after an invalid
upstream result. S3 archival precedes staging writes, each staging page is one
database transaction, manifests prevent repeat application, and watermarks
advance only with a successful page commit.

After correcting an infrastructure or configuration problem, use **Run
workflow** in GitHub Actions. The manually triggered run calls the same
watermark-driven implementation, so already committed data is skipped and any
remaining source records are processed safely.

The workflow updates S3, PostgreSQL staging, analytics, and reporting. It does
not refresh or republish the Power BI Import report.
