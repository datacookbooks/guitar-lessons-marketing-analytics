BEGIN;

-- last_cursor is the integer API next_since value. It is deliberately
-- independent of the text _raw_row_id stored on staging rows.
CREATE TABLE staging.etl_watermark (
    table_name text PRIMARY KEY,
    last_cursor bigint NOT NULL DEFAULT 0,
    last_successful_run_id text,
    last_successful_s3_key text,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT etl_watermark_known_table CHECK (
        table_name IN (
            'dim_plan',
            'dim_campaign',
            'dim_customer',
            'fact_subscription_period',
            'fact_payment',
            'fact_campaign_daily',
            'fact_campaign_assignment'
        )
    ),
    CONSTRAINT etl_watermark_cursor_nonnegative CHECK (last_cursor >= 0)
);

-- One row per successfully loaded S3 object. The primary key prevents a page
-- from being applied twice, even if the loader is rerun.
CREATE TABLE staging.etl_loaded_object (
    source_s3_key text PRIMARY KEY,
    table_name text NOT NULL,
    source_load_type text NOT NULL,
    source_run_id text NOT NULL,
    source_page_number integer NOT NULL,
    source_cursor_start bigint NOT NULL,
    source_cursor_end bigint NOT NULL,
    source_row_count integer NOT NULL,
    source_has_more boolean NOT NULL,
    extracted_at timestamptz NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT etl_loaded_object_known_table CHECK (
        table_name IN (
            'dim_plan',
            'dim_campaign',
            'dim_customer',
            'fact_subscription_period',
            'fact_payment',
            'fact_campaign_daily',
            'fact_campaign_assignment'
        )
    ),
    CONSTRAINT etl_loaded_object_page_positive CHECK (source_page_number > 0),
    CONSTRAINT etl_loaded_object_cursor_valid CHECK (
        source_cursor_start >= 0
        AND source_cursor_end >= source_cursor_start
    ),
    CONSTRAINT etl_loaded_object_row_count_nonnegative
        CHECK (source_row_count >= 0)
);

INSERT INTO staging.etl_watermark (table_name)
VALUES
    ('dim_plan'),
    ('dim_campaign'),
    ('dim_customer'),
    ('fact_subscription_period'),
    ('fact_payment'),
    ('fact_campaign_daily'),
    ('fact_campaign_assignment')
ON CONFLICT (table_name) DO NOTHING;

COMMIT;
