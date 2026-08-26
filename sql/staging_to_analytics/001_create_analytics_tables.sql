BEGIN;

-- Typed analytics tables retain selected source lineage while enforcing the
-- business keys, domains, and relationships defined by the cleaning contract.

CREATE TABLE IF NOT EXISTS analytics.dim_plan (
    plan_id smallint PRIMARY KEY,
    generated_for_date date,
    plan_name text,
    monthly_price numeric(10, 2),
    estimated_monthly_variable_cost numeric(10, 2),
    weekly_recorded_lesson_limit integer,
    private_sessions_per_month integer,
    is_paid_plan boolean,
    selected_raw_row_id text NOT NULL,
    source_s3_key text NOT NULL,
    source_run_id text NOT NULL,
    transformed_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT dim_plan_id_positive CHECK (plan_id > 0),
    CONSTRAINT dim_plan_name_nonblank CHECK (
        plan_name IS NULL OR BTRIM(plan_name) <> ''
    ),
    CONSTRAINT dim_plan_monthly_price_nonnegative CHECK (
        monthly_price IS NULL OR monthly_price >= 0
    ),
    CONSTRAINT dim_plan_variable_cost_nonnegative CHECK (
        estimated_monthly_variable_cost IS NULL
        OR estimated_monthly_variable_cost >= 0
    ),
    CONSTRAINT dim_plan_lesson_limit_nonnegative CHECK (
        weekly_recorded_lesson_limit IS NULL
        OR weekly_recorded_lesson_limit >= 0
    ),
    CONSTRAINT dim_plan_private_sessions_nonnegative CHECK (
        private_sessions_per_month IS NULL
        OR private_sessions_per_month >= 0
    )
);

CREATE INDEX IF NOT EXISTS dim_plan_paid_idx
    ON analytics.dim_plan (is_paid_plan, plan_id);

CREATE TABLE IF NOT EXISTS analytics.dim_campaign (
    campaign_id integer PRIMARY KEY,
    generated_for_date date,
    campaign_name text,
    channel text,
    objective text,
    active_start_date date,
    active_end_date date,
    default_treatment_share numeric(5, 4),
    is_evergreen boolean,
    selected_raw_row_id text NOT NULL,
    source_s3_key text NOT NULL,
    source_run_id text NOT NULL,
    transformed_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT dim_campaign_id_valid CHECK (
        campaign_id = -1 OR campaign_id > 0
    ),
    CONSTRAINT dim_campaign_name_nonblank CHECK (
        campaign_name IS NULL OR BTRIM(campaign_name) <> ''
    ),
    CONSTRAINT dim_campaign_channel_known CHECK (
        channel IS NULL
        OR channel IN ('email', 'paid search', 'paid social', 'unknown')
    ),
    CONSTRAINT dim_campaign_objective_known CHECK (
        objective IS NULL
        OR objective IN (
            'acquisition',
            'free-to-paid conversion',
            'unknown'
        )
    ),
    CONSTRAINT dim_campaign_dates_valid CHECK (
        active_end_date IS NULL
        OR active_start_date IS NULL
        OR active_end_date >= active_start_date
    ),
    CONSTRAINT dim_campaign_treatment_share_bounded CHECK (
        default_treatment_share IS NULL
        OR default_treatment_share BETWEEN 0 AND 1
    )
);

CREATE INDEX IF NOT EXISTS dim_campaign_channel_objective_idx
    ON analytics.dim_campaign (channel, objective);

-- Campaign -1 is the conformed destination for a present, parseable campaign
-- ID that does not match a source campaign dimension row.
INSERT INTO analytics.dim_campaign (
    campaign_id,
    generated_for_date,
    campaign_name,
    channel,
    objective,
    active_start_date,
    active_end_date,
    default_treatment_share,
    is_evergreen,
    selected_raw_row_id,
    source_s3_key,
    source_run_id
)
VALUES (
    -1,
    DATE '1900-01-01',
    'Unknown Campaign',
    'unknown',
    'unknown',
    DATE '1900-01-01',
    NULL,
    0,
    FALSE,
    '__unknown__',
    '__synthetic__',
    '__synthetic__'
)
ON CONFLICT (campaign_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS analytics.dim_customer (
    customer_id bigint PRIMARY KEY,
    generated_for_date date,
    prospect_key text,
    email text,
    signup_timestamp timestamptz,
    state text,
    experience_level text,
    initial_acquisition_channel text,
    first_campaign_id integer,
    source_updated_at timestamptz,
    selected_raw_row_id text NOT NULL,
    source_s3_key text NOT NULL,
    source_run_id text NOT NULL,
    transformed_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT dim_customer_id_positive CHECK (customer_id > 0),
    CONSTRAINT dim_customer_prospect_key_nonblank CHECK (
        prospect_key IS NULL OR BTRIM(prospect_key) <> ''
    ),
    CONSTRAINT dim_customer_email_normalized CHECK (
        email IS NULL
        OR (
            email = LOWER(BTRIM(email))
            AND email ~ '^[^[:space:]@]+@[^[:space:]@]+[.][^[:space:]@]+$'
        )
    ),
    CONSTRAINT dim_customer_state_valid CHECK (
        state IS NULL OR state ~ '^[A-Z]{2}$'
    ),
    CONSTRAINT dim_customer_experience_known CHECK (
        experience_level IS NULL
        OR experience_level IN ('beginner', 'intermediate', 'advanced')
    ),
    CONSTRAINT dim_customer_acquisition_known CHECK (
        initial_acquisition_channel IS NULL
        OR initial_acquisition_channel IN (
            'organic',
            'paid search',
            'paid social'
        )
    ),
    CONSTRAINT dim_customer_first_campaign_fk
        FOREIGN KEY (first_campaign_id)
        REFERENCES analytics.dim_campaign (campaign_id)
);

CREATE INDEX IF NOT EXISTS dim_customer_signup_idx
    ON analytics.dim_customer (signup_timestamp, customer_id);

CREATE INDEX IF NOT EXISTS dim_customer_first_campaign_idx
    ON analytics.dim_customer (first_campaign_id);

CREATE INDEX IF NOT EXISTS dim_customer_acquisition_idx
    ON analytics.dim_customer (initial_acquisition_channel, signup_timestamp);

CREATE TABLE IF NOT EXISTS analytics.fact_subscription_period (
    subscription_period_id text PRIMARY KEY,
    generated_for_date date,
    customer_id bigint,
    plan_id smallint,
    period_start_timestamp timestamptz,
    period_end_timestamp timestamptz,
    end_reason text,
    source_updated_at timestamptz,
    selected_raw_row_id text NOT NULL,
    source_s3_key text NOT NULL,
    source_run_id text NOT NULL,
    transformed_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fact_subscription_period_id_nonblank CHECK (
        BTRIM(subscription_period_id) <> ''
    ),
    CONSTRAINT fact_subscription_period_dates_valid CHECK (
        period_end_timestamp IS NULL
        OR period_start_timestamp IS NULL
        OR period_end_timestamp >= period_start_timestamp
    ),
    CONSTRAINT fact_subscription_period_end_pair_valid CHECK (
        (period_end_timestamp IS NULL AND end_reason IS NULL)
        OR (period_end_timestamp IS NOT NULL AND end_reason IS NOT NULL)
    ),
    CONSTRAINT fact_subscription_period_end_reason_known CHECK (
        end_reason IS NULL
        OR end_reason IN (
            'cancellation',
            'downgrade',
            'move to free',
            'upgrade'
        )
    ),
    CONSTRAINT fact_subscription_period_customer_fk
        FOREIGN KEY (customer_id)
        REFERENCES analytics.dim_customer (customer_id),
    CONSTRAINT fact_subscription_period_plan_fk
        FOREIGN KEY (plan_id)
        REFERENCES analytics.dim_plan (plan_id)
);

CREATE INDEX IF NOT EXISTS fact_subscription_period_customer_start_idx
    ON analytics.fact_subscription_period (
        customer_id,
        period_start_timestamp
    );

CREATE INDEX IF NOT EXISTS fact_subscription_period_plan_start_idx
    ON analytics.fact_subscription_period (plan_id, period_start_timestamp);

CREATE INDEX IF NOT EXISTS fact_subscription_period_end_idx
    ON analytics.fact_subscription_period (period_end_timestamp);

CREATE TABLE IF NOT EXISTS analytics.fact_payment (
    payment_id text PRIMARY KEY,
    generated_for_date date,
    subscription_period_id text,
    customer_id bigint,
    payment_timestamp timestamptz,
    amount numeric(10, 2),
    payment_type text,
    payment_status text,
    attempt_number integer,
    ingested_at timestamptz,
    selected_raw_row_id text NOT NULL,
    source_s3_key text NOT NULL,
    source_run_id text NOT NULL,
    transformed_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fact_payment_id_nonblank CHECK (BTRIM(payment_id) <> ''),
    CONSTRAINT fact_payment_type_known CHECK (
        payment_type IS NULL
        OR payment_type IN ('initial', 'renewal', 'retry', 'refund')
    ),
    CONSTRAINT fact_payment_status_known CHECK (
        payment_status IS NULL
        OR payment_status IN ('failed', 'succeeded')
    ),
    CONSTRAINT fact_payment_attempt_positive CHECK (
        attempt_number IS NULL OR attempt_number > 0
    ),
    CONSTRAINT fact_payment_subscription_period_fk
        FOREIGN KEY (subscription_period_id)
        REFERENCES analytics.fact_subscription_period (
            subscription_period_id
        ),
    CONSTRAINT fact_payment_customer_fk
        FOREIGN KEY (customer_id)
        REFERENCES analytics.dim_customer (customer_id)
);

CREATE INDEX IF NOT EXISTS fact_payment_customer_timestamp_idx
    ON analytics.fact_payment (customer_id, payment_timestamp);

CREATE INDEX IF NOT EXISTS fact_payment_subscription_period_idx
    ON analytics.fact_payment (subscription_period_id);

CREATE INDEX IF NOT EXISTS fact_payment_status_type_idx
    ON analytics.fact_payment (payment_status, payment_type);

CREATE TABLE IF NOT EXISTS analytics.fact_campaign_daily (
    metric_date date NOT NULL,
    campaign_id integer NOT NULL,
    generated_for_date date,
    impressions bigint,
    clicks bigint,
    spend numeric(12, 2),
    platform_attributed_conversions bigint,
    ingested_at timestamptz,
    selected_raw_row_id text NOT NULL,
    source_s3_key text NOT NULL,
    source_run_id text NOT NULL,
    transformed_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fact_campaign_daily_pk PRIMARY KEY (metric_date, campaign_id),
    CONSTRAINT fact_campaign_daily_impressions_nonnegative CHECK (
        impressions IS NULL OR impressions >= 0
    ),
    CONSTRAINT fact_campaign_daily_clicks_nonnegative CHECK (
        clicks IS NULL OR clicks >= 0
    ),
    CONSTRAINT fact_campaign_daily_spend_nonnegative CHECK (
        spend IS NULL OR spend >= 0
    ),
    CONSTRAINT fact_campaign_daily_conversions_nonnegative CHECK (
        platform_attributed_conversions IS NULL
        OR platform_attributed_conversions >= 0
    ),
    CONSTRAINT fact_campaign_daily_campaign_fk
        FOREIGN KEY (campaign_id)
        REFERENCES analytics.dim_campaign (campaign_id)
);

CREATE INDEX IF NOT EXISTS fact_campaign_daily_campaign_date_idx
    ON analytics.fact_campaign_daily (campaign_id, metric_date);

CREATE INDEX IF NOT EXISTS fact_campaign_daily_date_idx
    ON analytics.fact_campaign_daily (metric_date);

CREATE TABLE IF NOT EXISTS analytics.fact_campaign_assignment (
    assignment_id text PRIMARY KEY,
    generated_for_date date,
    campaign_id integer NOT NULL,
    measurement_window_id text,
    prospect_key text,
    customer_id bigint,
    assignment_arm text,
    assigned_at timestamptz,
    first_exposed_at timestamptz,
    source_updated_at timestamptz,
    selected_raw_row_id text NOT NULL,
    source_s3_key text NOT NULL,
    source_run_id text NOT NULL,
    transformed_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fact_campaign_assignment_id_nonblank CHECK (
        BTRIM(assignment_id) <> ''
    ),
    CONSTRAINT fact_campaign_assignment_window_valid CHECK (
        measurement_window_id IS NULL
        OR measurement_window_id ~ '^[0-9]{4}_Q[1-4]$'
    ),
    CONSTRAINT fact_campaign_assignment_prospect_nonblank CHECK (
        prospect_key IS NULL OR BTRIM(prospect_key) <> ''
    ),
    CONSTRAINT fact_campaign_assignment_arm_known CHECK (
        assignment_arm IS NULL
        OR assignment_arm IN ('treatment', 'holdout')
    ),
    CONSTRAINT fact_campaign_assignment_exposure_valid CHECK (
        first_exposed_at IS NULL
        OR assigned_at IS NULL
        OR first_exposed_at >= assigned_at
    ),
    CONSTRAINT fact_campaign_assignment_campaign_fk
        FOREIGN KEY (campaign_id)
        REFERENCES analytics.dim_campaign (campaign_id),
    CONSTRAINT fact_campaign_assignment_customer_fk
        FOREIGN KEY (customer_id)
        REFERENCES analytics.dim_customer (customer_id)
);

CREATE INDEX IF NOT EXISTS fact_campaign_assignment_experiment_idx
    ON analytics.fact_campaign_assignment (
        campaign_id,
        measurement_window_id,
        assignment_arm
    );

CREATE INDEX IF NOT EXISTS fact_campaign_assignment_customer_idx
    ON analytics.fact_campaign_assignment (customer_id);

CREATE TABLE IF NOT EXISTS analytics.data_quality_issue (
    source_table text NOT NULL,
    raw_row_id text NOT NULL,
    business_key text NOT NULL,
    column_name text NOT NULL,
    issue_code text NOT NULL,
    raw_value text,
    is_selected_delivery boolean NOT NULL,
    source_s3_key text NOT NULL,
    source_run_id text NOT NULL,
    detected_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT data_quality_issue_pk PRIMARY KEY (
        source_table,
        raw_row_id,
        column_name,
        issue_code
    ),
    CONSTRAINT data_quality_issue_source_table_known CHECK (
        source_table IN (
            'dim_plan',
            'dim_campaign',
            'dim_customer',
            'fact_subscription_period',
            'fact_payment',
            'fact_campaign_daily',
            'fact_campaign_assignment'
        )
    ),
    CONSTRAINT data_quality_issue_code_known CHECK (
        issue_code IN (
            'exact_duplicate',
            'superseded_delivery',
            'missing_value',
            'blank_value',
            'invalid_numeric',
            'invalid_date',
            'invalid_timestamp',
            'invalid_category',
            'unknown_reference',
            'inconsistent_null_pair'
        )
    )
);

CREATE INDEX IF NOT EXISTS data_quality_issue_code_idx
    ON analytics.data_quality_issue (issue_code, source_table);

CREATE INDEX IF NOT EXISTS data_quality_issue_business_key_idx
    ON analytics.data_quality_issue (source_table, business_key);

COMMIT;
