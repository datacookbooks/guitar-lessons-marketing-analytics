BEGIN;

-- Staging tables intentionally preserve source values as text. Constraints
-- apply only to ingestion identifiers and lineage, not to messy business data.

CREATE TABLE staging.dim_plan (
    _raw_row_id text PRIMARY KEY,
    _generated_for_date text,
    plan_id text,
    plan_name text,
    monthly_price text,
    estimated_monthly_variable_cost text,
    weekly_recorded_lesson_limit text,
    private_sessions_per_month text,
    is_paid_plan text,
    source_s3_key text NOT NULL,
    source_run_id text NOT NULL,
    source_load_type text NOT NULL,
    source_page_number integer NOT NULL,
    source_cursor_start bigint NOT NULL,
    source_cursor_end bigint NOT NULL,
    extracted_at timestamptz NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT dim_plan_source_page_positive CHECK (source_page_number > 0),
    CONSTRAINT dim_plan_source_cursor_valid CHECK (
        source_cursor_start >= 0
        AND source_cursor_end >= source_cursor_start
    )
);

CREATE TABLE staging.dim_campaign (
    _raw_row_id text PRIMARY KEY,
    _generated_for_date text,
    campaign_id text,
    campaign_name text,
    channel text,
    objective text,
    primary_conversion_event text,
    active_start_date text,
    active_end_date text,
    default_treatment_share text,
    is_evergreen text,
    source_s3_key text NOT NULL,
    source_run_id text NOT NULL,
    source_load_type text NOT NULL,
    source_page_number integer NOT NULL,
    source_cursor_start bigint NOT NULL,
    source_cursor_end bigint NOT NULL,
    extracted_at timestamptz NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT dim_campaign_source_page_positive CHECK (source_page_number > 0),
    CONSTRAINT dim_campaign_source_cursor_valid CHECK (
        source_cursor_start >= 0
        AND source_cursor_end >= source_cursor_start
    )
);

CREATE TABLE staging.dim_customer (
    _raw_row_id text PRIMARY KEY,
    _generated_for_date text,
    customer_id text,
    prospect_key text,
    email text,
    signup_timestamp text,
    state text,
    experience_level text,
    initial_acquisition_channel text,
    first_campaign_id text,
    source_updated_at text,
    source_s3_key text NOT NULL,
    source_run_id text NOT NULL,
    source_load_type text NOT NULL,
    source_page_number integer NOT NULL,
    source_cursor_start bigint NOT NULL,
    source_cursor_end bigint NOT NULL,
    extracted_at timestamptz NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT dim_customer_source_page_positive CHECK (source_page_number > 0),
    CONSTRAINT dim_customer_source_cursor_valid CHECK (
        source_cursor_start >= 0
        AND source_cursor_end >= source_cursor_start
    )
);

CREATE TABLE staging.fact_subscription_period (
    _raw_row_id text PRIMARY KEY,
    _generated_for_date text,
    subscription_period_id text,
    customer_id text,
    plan_id text,
    period_start_timestamp text,
    period_end_timestamp text,
    end_reason text,
    source_updated_at text,
    source_s3_key text NOT NULL,
    source_run_id text NOT NULL,
    source_load_type text NOT NULL,
    source_page_number integer NOT NULL,
    source_cursor_start bigint NOT NULL,
    source_cursor_end bigint NOT NULL,
    extracted_at timestamptz NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fact_subscription_period_source_page_positive
        CHECK (source_page_number > 0),
    CONSTRAINT fact_subscription_period_source_cursor_valid CHECK (
        source_cursor_start >= 0
        AND source_cursor_end >= source_cursor_start
    )
);

CREATE TABLE staging.fact_payment (
    _raw_row_id text PRIMARY KEY,
    _generated_for_date text,
    payment_id text,
    subscription_period_id text,
    customer_id text,
    payment_timestamp text,
    amount text,
    payment_type text,
    payment_status text,
    attempt_number text,
    ingested_at text,
    source_s3_key text NOT NULL,
    source_run_id text NOT NULL,
    source_load_type text NOT NULL,
    source_page_number integer NOT NULL,
    source_cursor_start bigint NOT NULL,
    source_cursor_end bigint NOT NULL,
    extracted_at timestamptz NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fact_payment_source_page_positive CHECK (source_page_number > 0),
    CONSTRAINT fact_payment_source_cursor_valid CHECK (
        source_cursor_start >= 0
        AND source_cursor_end >= source_cursor_start
    )
);

CREATE TABLE staging.fact_campaign_daily (
    _raw_row_id text PRIMARY KEY,
    _generated_for_date text,
    metric_date text,
    campaign_id text,
    impressions text,
    clicks text,
    spend text,
    platform_attributed_conversions text,
    ingested_at text,
    source_s3_key text NOT NULL,
    source_run_id text NOT NULL,
    source_load_type text NOT NULL,
    source_page_number integer NOT NULL,
    source_cursor_start bigint NOT NULL,
    source_cursor_end bigint NOT NULL,
    extracted_at timestamptz NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fact_campaign_daily_source_page_positive
        CHECK (source_page_number > 0),
    CONSTRAINT fact_campaign_daily_source_cursor_valid CHECK (
        source_cursor_start >= 0
        AND source_cursor_end >= source_cursor_start
    )
);

CREATE TABLE staging.fact_campaign_assignment (
    _raw_row_id text PRIMARY KEY,
    _generated_for_date text,
    assignment_id text,
    campaign_id text,
    measurement_window_id text,
    prospect_key text,
    customer_id text,
    assignment_arm text,
    assigned_at text,
    first_exposed_at text,
    source_updated_at text,
    source_s3_key text NOT NULL,
    source_run_id text NOT NULL,
    source_load_type text NOT NULL,
    source_page_number integer NOT NULL,
    source_cursor_start bigint NOT NULL,
    source_cursor_end bigint NOT NULL,
    extracted_at timestamptz NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fact_campaign_assignment_source_page_positive
        CHECK (source_page_number > 0),
    CONSTRAINT fact_campaign_assignment_source_cursor_valid CHECK (
        source_cursor_start >= 0
        AND source_cursor_end >= source_cursor_start
    )
);

COMMIT;
