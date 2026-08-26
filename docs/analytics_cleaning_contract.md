# Analytics Cleaning Contract

## Scope

This document defines how the seven source-shaped `staging` tables become
typed, deduplicated `analytics` dimensions and facts. It records the live RDS
profile completed on 2026-08-25 and fixes the cleaning, key, relationship,
quality, and rerun rules that the SQL implementation must follow.

This milestone does not define churn, retention, CLV, campaign ROI, or other
reporting metrics. Those belong in the later `reporting` layer.

## Profiled staging baseline

The profile ran through a PostgreSQL connection configured with
`default_transaction_read_only=on`.

| Table | Staging rows | Distinct raw IDs | Business-key groups | Excess deliveries | Exact-duplicate excess |
|---|---:|---:|---:|---:|---:|
| `dim_plan` | 3 | 3 | 3 | 0 | 0 |
| `dim_campaign` | 3 | 3 | 3 | 0 | 0 |
| `dim_customer` | 11,583 | 11,583 | 11,273 | 310 | 132 |
| `fact_subscription_period` | 15,951 | 15,951 | 14,585 | 1,366 | 181 |
| `fact_payment` | 49,022 | 49,022 | 48,477 | 545 | 545 |
| `fact_campaign_daily` | 2,992 | 2,992 | 2,904 | 88 | 40 |
| `fact_campaign_assignment` | 90,591 | 90,591 | 89,560 | 1,031 | 977 |
| **Total** | **170,145** | **170,145** | **166,805** | **3,340** | **1,875** |

The database also contained 178 successfully loaded S3 objects and the seven
expected table watermarks. No table had a missing business key.

The difference between business-key excess and exact-duplicate excess
identifies deliveries whose business values changed:

| Table | Non-exact excess deliveries | Interpretation |
|---|---:|---|
| `dim_customer` | 178 | Later customer versions |
| `fact_subscription_period` | 1,185 | Later subscription-period versions |
| `fact_campaign_daily` | 48 | Late spend corrections |
| `fact_campaign_assignment` | 54 | Later assignment versions |

All 545 excess payment deliveries were exact repeats. Payment event time must
not be used to invent separate events for those deliveries.

## Observed nulls, blanks, and variants

| Column | Nulls | Blanks | Contract |
|---|---:|---:|---|
| `dim_plan.weekly_recorded_lesson_limit` | 2 | 0 | Valid `NULL`: unlimited recorded lessons |
| `dim_campaign.active_end_date` | 3 | 0 | Valid `NULL`: campaign remains active |
| `dim_customer.email` | 62 | 62 | Trim and lowercase; blank becomes `NULL`; missing/invalid email is a quality issue, not a rejected customer |
| `dim_customer.first_campaign_id` | 6,931 | 0 | Valid optional relationship |
| `fact_subscription_period.period_end_timestamp` | 11,585 | 0 | Valid only for an open period |
| `fact_subscription_period.end_reason` | 11,585 | 0 | Valid only for an open period; must agree with the end timestamp |
| `fact_campaign_daily.clicks` | 0 | 25 | Blank becomes typed `NULL` and a quality issue |
| `fact_campaign_assignment.customer_id` | 74,121 | 0 | Valid for a prospect who has not registered |
| `fact_campaign_assignment.first_exposed_at` | 26,049 | 0 | Valid for holdout or unexposed assignments |

The following fields contain only case or surrounding-whitespace variants.
Use `BTRIM` plus the documented output case:

| Column | Raw labels | Normalized labels | Standard output |
|---|---:|---:|---|
| `dim_customer.state` | 39 | 13 | Uppercase two-letter code |
| `dim_customer.experience_level` | 9 | 3 | `beginner`, `intermediate`, `advanced` |
| `dim_customer.initial_acquisition_channel` | 9 | 3 | `organic`, `paid search`, `paid social` |
| `fact_campaign_assignment.assignment_arm` | 4 | 2 | `treatment`, `holdout` |

Plan names and campaign display names retain their approved display case.
Campaign channel and objective labels are trimmed and stored in lowercase.
Subscription end reasons, payment types, and payment statuses are trimmed and
stored in lowercase. Their profiled normalized domains are:

- channel: `email`, `paid search`, `paid social`;
- objective: `acquisition`, `free-to-paid conversion`;
- end reason: `cancellation`, `downgrade`, `move to free`, `upgrade`;
- payment type: `initial`, `renewal`, `retry`, `refund`; and
- payment status: `failed`, `succeeded`.

## Guarded conversions

Every raw value is trimmed and converted with a `CASE` expression whose first
branch validates the text. SQL must not rely on the evaluation order of a
Boolean `AND` to protect a cast. `NULLIF(BTRIM(value), '')` handles blanks
before validation.

All profiled date and timestamp values were valid. The source's timestamps
that represent instants become `timestamptz`; campaign active dates,
`metric_date`, and `_generated_for_date` become `date`.

The following nonblank values need special treatment:

| Source column | Raw condition | Raw deliveries | Typed result | Quality result |
|---|---|---:|---|---|
| `dim_plan.weekly_recorded_lesson_limit` | `2.0` | 1 | Accept as integer-like and store `2` | No issue |
| `fact_payment.amount` | `not_available` | 189 | `NULL` | `invalid_numeric` |
| `fact_campaign_daily.spend` | `unknown` | 10 | `NULL` unless another valid delivery wins for the same business key | `invalid_numeric` |

An integer-like conversion accepts digits with an optional `.0` suffix only;
it does not round non-integral decimals. Monetary values must fit the target
precision and scale. Refund amounts may be negative; campaign spend and count
measures must be nonnegative.

## Analytics tables and keys

The analytics layer retains the seven source grains and business keys:

| Analytics table | Primary/business key | Required lineage |
|---|---|---|
| `analytics.dim_plan` | `plan_id` | selected `_raw_row_id` |
| `analytics.dim_campaign` | `campaign_id` | selected `_raw_row_id` |
| `analytics.dim_customer` | `customer_id` | selected `_raw_row_id`, typed `source_updated_at` |
| `analytics.fact_subscription_period` | `subscription_period_id` | selected `_raw_row_id`, typed `source_updated_at` |
| `analytics.fact_payment` | `payment_id` | selected `_raw_row_id`, typed `ingested_at` |
| `analytics.fact_campaign_daily` | `metric_date`, `campaign_id` | selected `_raw_row_id`, typed `ingested_at` |
| `analytics.fact_campaign_assignment` | `assignment_id` | selected `_raw_row_id`, typed `source_updated_at` |

`selected_raw_row_id` is text in analytics because it is source lineage, not a
business or API cursor. It permits a selected record to be traced back to its
staging row and S3 object. Analytics rows also retain `source_s3_key` and
`source_run_id` from the selected delivery.

## Deterministic deduplication

Use `ROW_NUMBER()` partitioned by the analytics business key. Ordering is
descending so rank 1 is the selected delivery:

| Table | Deduplication order |
|---|---|
| `dim_plan` | valid generated date, `extracted_at`, numeric raw-delivery ID |
| `dim_campaign` | valid generated date, `extracted_at`, numeric raw-delivery ID |
| `dim_customer` | valid `source_updated_at`, `extracted_at`, numeric raw-delivery ID |
| `fact_subscription_period` | valid `source_updated_at`, `extracted_at`, numeric raw-delivery ID |
| `fact_payment` | valid `ingested_at`, `extracted_at`, numeric raw-delivery ID |
| `fact_campaign_daily` | row validity, valid `ingested_at`, `extracted_at`, numeric raw-delivery ID |
| `fact_campaign_assignment` | valid `source_updated_at`, `extracted_at`, numeric raw-delivery ID |

The staging contract guarantees that `_raw_row_id` contains ASCII digits, so
its numeric value is a deterministic delivery-order tie-breaker. Business
event timestamps such as `signup_timestamp`, `payment_timestamp`,
`period_start_timestamp`, and `assigned_at` never substitute for the API
cursor or raw-delivery order.

For `fact_campaign_daily`, all 88 duplicated campaign/date groups contained
one excess delivery. Forty groups were exact repeats. The remaining 48 groups
had a later `ingested_at` and a changed `spend`; impressions, clicks, and
attributed conversions did not change. The latest fully valid delivery wins.
If a key has no fully valid delivery, retain its latest delivery with invalid
measures converted to `NULL` and record the relevant quality issues.

## Relationship policy

The profile found zero unmatched rows for every implemented relationship
except campaign assignments:

| Relationship | Unmatched raw deliveries |
|---|---:|
| Customer first campaign to campaign | 0 |
| Subscription to customer | 0 |
| Subscription to plan | 0 |
| Payment to subscription period | 0 |
| Payment to customer | 0 |
| Campaign daily to campaign | 0 |
| Assignment to customer | 0 |
| Assignment to campaign | 285 |

The unmatched assignment values are the controlled unknown campaign ID. Use
an explicit `analytics.dim_campaign` unknown member with `campaign_id = -1`.
Map a parseable but unmatched campaign reference to `-1`, retain the raw value
in the associated quality record, and keep the assignment in analytics for
intention-to-treat analysis.

Optional customer and campaign links remain `NULL` when the source value is
truly absent. A present but unparseable or unmatched optional value also
becomes `NULL`, but it must create a quality record so it is distinguishable
from a legitimate absence. Required relationships that cannot be resolved
must not make the full transaction fail; retain the row when its grain remains
identifiable, use the documented unknown or nullable target, and record the
issue.

## Quality issue path

Add an analytics support table, separate from the seven dimensions and facts,
with one row per detected issue. Its natural uniqueness must include:

- source table;
- `_raw_row_id`;
- column or rule name; and
- issue code.

The table records the business-key text, raw value when applicable, issue
code, and selected/superseded status. Required issue codes include:

- `exact_duplicate`;
- `superseded_delivery`;
- `missing_value`;
- `blank_value`;
- `invalid_numeric`;
- `invalid_date`;
- `invalid_timestamp`;
- `invalid_category`;
- `unknown_reference`; and
- `inconsistent_null_pair`.

Quality records make rejected values, superseded versions, and relationship
problems available to later `reporting` views without putting raw text into
typed analytics measures.

## Idempotent, correction-aware loading

The analytics DDL and transformations are rerunnable. Apply the ordered DDL
files together in one migration transaction. After the DDL exists, each full
staging-to-analytics transformation uses a separate PostgreSQL transaction:

1. calculate guarded, standardized staging CTEs;
2. rank and select one row per business key;
3. upsert dimensions in dependency order;
4. upsert facts in dependency order; and
5. upsert source-derived quality issues.

Fact and dimension upserts use their documented primary keys with
`INSERT ... ON CONFLICT ... DO UPDATE`. Updates are applied only when the
incoming selected raw-delivery ID or typed values differ. A second run over
unchanged staging data must leave row counts, selected lineage, typed values,
relationships, and quality-issue counts unchanged.

Staging is append-only, so a source-derived quality issue remains valid
history even when a later delivery corrects that business key. The quality
table therefore does not need an unscoped cleanup delete during a rerun.

The implementation must not use `DROP TABLE`, `DROP SCHEMA`, `TRUNCATE`, or an
unqualified destructive statement. Applying analytics SQL never changes
staging rows, S3 manifests, or API watermarks.

## Implemented production validation

The contract was implemented in the following versioned SQL files:

- `sql/staging_to_analytics/001_create_analytics_tables.sql`;
- `sql/staging_to_analytics/002_transform_staging_to_analytics.sql`.

On 2026-08-26, the guarded runner applied both files to RDS PostgreSQL and
committed the following reconciled state:

| Analytics table | Rows |
|---|---:|
| `dim_plan` | 3 |
| `dim_campaign` | 4 |
| `dim_customer` | 11,273 |
| `fact_subscription_period` | 14,585 |
| `fact_payment` | 48,477 |
| `fact_campaign_daily` | 2,904 |
| `fact_campaign_assignment` | 89,560 |
| `data_quality_issue` | 5,848 |

The four campaign rows include the three source campaigns and the synthetic
unknown campaign member. Quality issues reconciled to 87 blank values, 1,875
exact duplicates, 199 invalid numerics, 62 missing values, 3,340 superseded
deliveries, and 285 unknown references.

The runner then executed the complete transformation a second time over
unchanged staging. Full-table fingerprints, including lineage and audit
timestamps, remained identical, confirming that the rerun committed no data
changes. The opt-in rollback integration test also passed independently,
proving the same SQL and reconciliation checks against live RDS without
persisting its test transaction.
