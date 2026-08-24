# Source and Staging Data Dictionary

## Scope

This document defines the seven raw source datasets, the implemented Amazon S3
page wrapper, and the PostgreSQL `staging` contract. It is based on the actual
historical S3 delivery inspected on 2026-08-24, not on an older proposed object
layout.

The source intentionally contains controlled quality problems. Staging keeps
those values unchanged so that the SQL transformation layer can measure and
resolve them transparently. Typed values, standardized categories, business-key
deduplication, and relationship enforcement belong in the later `analytics`
schema.

## S3 page contract

### Implemented object key

```text
raw/<load_type>/<table_name>/
  extract_date=YYYY-MM-DD/
  run_id=YYYYMMDDTHHMMSSffffffZ/
  page-NNNNN.json
```

The database stores the object key without the private bucket name.
`source_run_id` is derived from the `run_id=` key segment because the run ID is
not repeated inside the JSON wrapper.

### JSON wrapper

| Location | Field | Type | Meaning |
|---|---|---|---|
| `metadata` | `table_name` | string | Source table represented by the page |
| `metadata` | `load_type` | string | Extraction mode, currently `historical` |
| `metadata` | `page_number` | integer | One-based page number within the table/run |
| `metadata` | `extracted_at` | UTC timestamp string | Shared extraction timestamp used to construct the S3 run ID |
| `response` | `table_name` | string | API table name; must agree with the metadata and key |
| `response` | `since` | integer | Cursor supplied to the API for this request |
| `response` | `data` | array | Raw source rows |
| `response` | `count` | integer | Number of rows in `data` |
| `response` | `next_since` | integer | Cursor to use for the next request |
| `response` | `has_more` | Boolean | Whether another page follows |

### Cursor and raw-row distinction

In the inspected JSON, `_raw_row_id` is encoded as an integer. The staging
loader accepts a nonnegative integer or digit-only string for that field and
stores it as text. The remaining business fields must be strings or JSON
`null`, matching the observed source contract. An unexpected number, Boolean,
array, or object in a business field raises an error instead of being silently
converted to text. This makes source type drift visible before loading.

The API cursor remains separate:

- staging rows store `_raw_row_id` as text and use it as the per-table
  idempotency key;
- page lineage stores `source_cursor_start` and `source_cursor_end` as
  `bigint`; and
- `staging.etl_watermark.last_cursor` stores the last successfully committed
  `next_since` value.

The loader must never calculate a watermark by casting or taking the maximum of
the staged `_raw_row_id` text.

## Shared staging lineage

Every staging table includes the following columns in addition to its source
columns.

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `source_s3_key` | `text` | Full object key excluding the bucket name |
| `source_run_id` | `text` | Run ID parsed from the object key |
| `source_load_type` | `text` | Load mode from wrapper metadata |
| `source_page_number` | `integer` | Page number from wrapper metadata |
| `source_cursor_start` | `bigint` | API `since` value for the page |
| `source_cursor_end` | `bigint` | API `next_since` value for the page |
| `extracted_at` | `timestamptz` | Extraction time from wrapper metadata |
| `loaded_at` | `timestamptz` | Time the row was loaded into PostgreSQL |

The seven tables have no staging foreign keys or business-value checks. An
unknown campaign, malformed number, blank value, or intentionally duplicated
business row must be loadable and countable.

## Table summary

| Source table | Intended grain | Business key | Staging idempotency key |
|---|---|---|---|
| `dim_plan` | One row per subscription plan | `plan_id` | `_raw_row_id` |
| `dim_campaign` | One row per campaign | `campaign_id` | `_raw_row_id` |
| `dim_customer` | One row per registered customer | `customer_id` | `_raw_row_id` |
| `fact_subscription_period` | One continuous customer-plan period | `subscription_period_id` | `_raw_row_id` |
| `fact_payment` | One payment attempt, retry, charge, or refund | `payment_id` | `_raw_row_id` |
| `fact_campaign_daily` | One campaign and metric date | `metric_date`, `campaign_id` | `_raw_row_id` |
| `fact_campaign_assignment` | One eligible person in one campaign measurement window | `assignment_id` | `_raw_row_id` |

Business keys are deliberately not unique in staging. Multiple raw deliveries
of the same business event must survive ingestion for later deduplication.

## Common source columns

| Column | Raw behavior | Intended analytics type | Notes |
|---|---|---|---|
| `_raw_row_id` | JSON integer; converted to staging text | Not a business field | Stable source-delivery identifier and per-table staging primary key |
| `_generated_for_date` | Date-shaped string | `date` | Simulated business date through which the record was generated |

## `dim_plan`

Grain: one row per subscription plan. Business key: `plan_id`.

| Source column | Meaning | Raw behavior | Intended analytics type / cleaning |
|---|---|---|---|
| `plan_id` | Plan identifier | Numeric-shaped text | `smallint` after guarded cast |
| `plan_name` | Display name | Text such as `Free`, `Pro`, or `Master` | Trimmed text |
| `monthly_price` | Monthly customer price | Decimal-shaped text | `numeric(10,2)` after guarded cast |
| `estimated_monthly_variable_cost` | Estimated monthly service cost | Decimal-shaped text | `numeric(10,2)` after guarded cast |
| `weekly_recorded_lesson_limit` | Weekly recorded-lesson cap | Numeric-shaped text or `null` | Integer; `null` means unlimited |
| `private_sessions_per_month` | Included private sessions | Numeric-shaped text | Integer after guarded cast |
| `is_paid_plan` | Whether the plan charges a subscription fee | Boolean-shaped text such as `True` | Boolean after case-insensitive validation |

Valid business behavior: the Free plan has a zero price; paid plans can have a
`null` recorded-lesson limit because their recorded lessons are unlimited.

## `dim_campaign`

Grain: one row per marketing campaign. Business key: `campaign_id`.

| Source column | Meaning | Raw behavior | Intended analytics type / cleaning |
|---|---|---|---|
| `campaign_id` | Campaign identifier | Numeric-shaped text | Integer after guarded cast |
| `campaign_name` | Campaign display name | Text | Trim whitespace; preserve approved display name |
| `channel` | Delivery channel | Text | Standardize known channel labels |
| `objective` | Acquisition or conversion purpose | Text | Standardize known objectives |
| `active_start_date` | First active date | Timestamp-shaped text | `date` after guarded conversion |
| `active_end_date` | Last active date | Timestamp-shaped text or `null` | `date`; `null` means still active |
| `default_treatment_share` | Default fraction assigned to treatment | Decimal-shaped text | Bounded numeric after guarded cast |
| `is_evergreen` | Evergreen-campaign flag | Boolean-shaped text | Boolean after case-insensitive validation |

## `dim_customer`

Grain: one row per registered customer. Business key: `customer_id`.

| Source column | Meaning | Raw behavior | Intended analytics type / cleaning |
|---|---|---|---|
| `customer_id` | Customer identifier | Integer-shaped text | `bigint` after guarded cast |
| `prospect_key` | Pre-registration person key | Text | Trimmed text; supports assignment-to-customer linkage |
| `email` | Customer email | Text, blank string, or `null` | Trim, lowercase, convert blank to `null`, validate separately |
| `signup_timestamp` | Registration time | Timezone-aware timestamp text | `timestamptz` after guarded cast |
| `state` | Two-letter US state code | Text | Trim, uppercase, validate against accepted codes |
| `experience_level` | Guitar experience category | Text with possible casing/spacing variants | Trim and standardize known labels |
| `initial_acquisition_channel` | Initial acquisition source | Text with possible casing/spacing variants | Trim and standardize known labels |
| `first_campaign_id` | First associated campaign, if any | `null` or numeric text that may end in `.0` | Integer after guarded integer-like conversion |
| `source_updated_at` | Source record update time | Timezone-aware timestamp text | `timestamptz` used for latest-record deduplication |

Observed in the representative page: six JSON-null emails, four blank emails,
and 997 distinct `customer_id` values among 1,000 raw rows. Staging retains all
of them. The analytics model will rank records by `source_updated_at` and raw
lineage before choosing one row per customer.

## `fact_subscription_period`

Grain: one continuous period during which a customer remains on one plan.
Business key: `subscription_period_id`.

| Source column | Meaning | Raw behavior | Intended analytics type / cleaning |
|---|---|---|---|
| `subscription_period_id` | Continuous plan-period identifier | Text | Trimmed text |
| `customer_id` | Customer identifier | Integer-shaped text | `bigint` after guarded cast |
| `plan_id` | Plan in effect during the period | Numeric-shaped text | `smallint` after guarded cast |
| `period_start_timestamp` | Period start | Timezone-aware timestamp text | `timestamptz` after guarded cast |
| `period_end_timestamp` | Period end | Timestamp text or `null` | `timestamptz`; `null` means open period |
| `end_reason` | Reason a closed period ended | Text or `null` | Standardize cancellation, paid-to-Free, upgrade, and downgrade values |
| `source_updated_at` | Source update time | Timezone-aware timestamp text | `timestamptz` used for latest-record deduplication |

Observed in the representative page: 999 distinct business keys among 1,000
raw rows. Open subscriptions correctly have both a null end timestamp and null
end reason. Movement between Pro and Master is an upgrade or downgrade; paid
churn is cancellation or movement from a paid plan to Free.

## `fact_payment`

Grain: one payment attempt, retry, successful charge, or refund. Business key:
`payment_id`.

| Source column | Meaning | Raw behavior | Intended analytics type / cleaning |
|---|---|---|---|
| `payment_id` | Payment-event identifier | Text | Trimmed text |
| `subscription_period_id` | Related subscription period | Text | Trimmed text; relationship checked in analytics |
| `customer_id` | Related customer | Integer-shaped text | `bigint` after guarded cast |
| `payment_timestamp` | Business-event time | Timezone-aware timestamp text | `timestamptz` after guarded cast |
| `amount` | Signed payment amount | Decimal-shaped text | `numeric(10,2)` after guarded cast |
| `payment_type` | Initial, renewal, retry, or refund event | Text | Trim, lowercase, standardize known values |
| `payment_status` | Attempt outcome | Text | Trim, lowercase, standardize known values |
| `attempt_number` | Attempt number within a charge sequence | Numeric-shaped text | Positive integer after guarded cast |
| `ingested_at` | Source ingestion/update time | Timezone-aware timestamp text | `timestamptz` used for ordering deliveries |

A negative amount is valid for a refund and must not automatically be labeled
as bad data. Failed attempts and later retries are separate events used to
calculate recovery rates.

## `fact_campaign_daily`

Grain: one campaign and metric date after late-correction resolution. Raw
deliveries may contain several versions of that business key. Business key:
`metric_date`, `campaign_id`.

| Source column | Meaning | Raw behavior | Intended analytics type / cleaning |
|---|---|---|---|
| `metric_date` | Campaign reporting date | Midnight timestamp-shaped text | `date` after guarded conversion |
| `campaign_id` | Campaign identifier | Numeric-shaped text | Integer after guarded cast |
| `impressions` | Delivered impressions | Numeric-shaped or deliberately malformed text | Nonnegative integer after guarded cast |
| `clicks` | Delivered clicks | Numeric-shaped, blank, or deliberately malformed text | Nonnegative integer; blanks/invalids become `null` and quality events |
| `spend` | Campaign spend | Decimal-shaped or deliberately malformed text | Nonnegative `numeric(12,2)` after guarded cast |
| `platform_attributed_conversions` | Platform-reported conversions | Numeric-shaped text | Nonnegative integer after guarded cast |
| `ingested_at` | Time this version entered the source | Timezone-aware timestamp text | `timestamptz`; latest valid version wins |

Observed in the representative page: eight blank `clicks` values. Zero spend
is valid for the Email campaign. Late spend corrections create multiple raw
versions of the same campaign/date; analytics must select the latest version by
`ingested_at` rather than summing versions.

## `fact_campaign_assignment`

Grain: one eligible person assigned within one campaign measurement window.
Business key: `assignment_id`.

| Source column | Meaning | Raw behavior | Intended analytics type / cleaning |
|---|---|---|---|
| `assignment_id` | Stable experiment-assignment identifier | Text | Trimmed text |
| `campaign_id` | Assigned campaign | Numeric-shaped text, including controlled unknown IDs | Integer after guarded cast; unmatched IDs become quality events |
| `measurement_window_id` | Campaign measurement window | Text such as `2024_Q3` | Validate documented year-quarter format |
| `prospect_key` | Eligible-person key | Text | Trimmed text; available before registration |
| `customer_id` | Registered customer, when conversion occurred | `null` or numeric text that may end in `.0` | Nullable `bigint` after guarded integer-like conversion |
| `assignment_arm` | Treatment or holdout | Text with controlled casing/whitespace variants | Trim and standardize to treatment/holdout |
| `assigned_at` | Random-assignment time | Timezone-aware timestamp text | `timestamptz` after guarded cast |
| `first_exposed_at` | First treatment exposure | Timestamp text or `null` | Nullable `timestamptz`; null is expected for holdouts and unexposed assignments |
| `source_updated_at` | Source update time | Timezone-aware timestamp text | `timestamptz` used for latest-record deduplication |

Observed in the representative page: assignment-arm variants included
`Treatment`, `Holdout`, and whitespace/case variants; campaign ID `9999` was an
intentional unmatched value. Assignment analysis follows intention-to-treat,
so rows remain in their originally assigned arms regardless of exposure.

## Controlled defects and planned treatment

| Raw condition | Staging behavior | Later analytics behavior |
|---|---|---|
| Repeated business keys or exact-duplicate deliveries with different raw IDs | Retain every raw delivery | Rank/deduplicate with documented business keys and update timestamps |
| Same `_raw_row_id` encountered again during a replay | Ignore or upsert safely on the staging primary key | No duplicate staging row |
| Blank or null email/click values | Preserve exactly | Convert blank to null, validate, and report quality counts |
| Casing and surrounding whitespace differences | Preserve exactly | `TRIM` and standardize known categories |
| `unknown`, `not_available`, or other malformed numeric text | Preserve exactly | Regex-guard casts; invalid values become null and quality events |
| Late campaign-spend correction | Retain every version | Choose the latest version by `ingested_at` for each campaign/date |
| Unknown campaign ID | Retain without staging foreign-key failure | Exclude or quarantine from matched metrics and report the mismatch |
| Out-of-order business timestamps | Load in cursor order and retain timestamps | Order business analysis by typed event/update timestamps |

## Idempotency and commit rule

`staging.etl_loaded_object.source_s3_key` records each successfully applied S3
page. Each staging table also has a primary key on `_raw_row_id`. These provide
object-level and row-level replay protection.

For a table load, the future loader must use one database transaction to:

1. insert the page's staging rows with duplicate-safe behavior;
2. record the page in `staging.etl_loaded_object`; and
3. update `staging.etl_watermark.last_cursor` to the page's
   `source_cursor_end`.

If any required write fails, the transaction rolls back and the watermark does
not advance.
