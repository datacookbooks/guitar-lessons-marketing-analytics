BEGIN;

-- This NOLOGIN role owns the reusable Power BI privilege set.
-- A separate LOGIN role and its password are created operationally outside
-- Git, then granted membership in this role.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_roles
        WHERE rolname = 'marketing_analytics_bi_reader'
    ) THEN
        CREATE ROLE marketing_analytics_bi_reader
            NOLOGIN
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            NOREPLICATION
            NOBYPASSRLS;
    ELSE
        ALTER ROLE marketing_analytics_bi_reader
            NOLOGIN
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            NOREPLICATION
            NOBYPASSRLS;
    END IF;
END
$$;

-- Use the database selected by the deployment connection instead of placing
-- an environment-specific database name in version control.
DO $$
BEGIN
    EXECUTE format(
        'GRANT CONNECT ON DATABASE %I TO marketing_analytics_bi_reader',
        current_database()
    );

    EXECUTE format(
        'REVOKE CREATE ON DATABASE %I FROM marketing_analytics_bi_reader',
        current_database()
    );
END
$$;

-- The BI role must not access ingestion data or ETL control state.
REVOKE ALL PRIVILEGES
    ON SCHEMA staging
    FROM marketing_analytics_bi_reader;

REVOKE ALL PRIVILEGES
    ON ALL TABLES IN SCHEMA staging
    FROM marketing_analytics_bi_reader;

REVOKE ALL PRIVILEGES
    ON ALL SEQUENCES IN SCHEMA staging
    FROM marketing_analytics_bi_reader;

REVOKE ALL PRIVILEGES
    ON ALL FUNCTIONS IN SCHEMA staging
    FROM marketing_analytics_bi_reader;

-- The BI role may resolve approved objects but may not create objects in the
-- analytical schemas.
REVOKE CREATE
    ON SCHEMA analytics, reporting
    FROM marketing_analytics_bi_reader;

GRANT USAGE
    ON SCHEMA analytics, reporting
    TO marketing_analytics_bi_reader;

-- Maintain an explicit allowlist. Do not replace this with grants on every
-- current or future table in either schema.
GRANT SELECT ON TABLE
    analytics.dim_plan,
    analytics.dim_campaign,
    analytics.vw_reporting_cutoff,
    reporting.vw_monthly_paid_movement,
    reporting.vw_paid_cohort_retention,
    reporting.vw_payment_recovery,
    reporting.vw_customer_value,
    reporting.vw_paid_clv_monthly_component,
    reporting.vw_expected_12m_paid_clv,
    reporting.vw_campaign_daily_performance,
    reporting.vw_campaign_experiment_arm,
    reporting.vw_campaign_incremental_performance,
    reporting.vw_data_quality
TO marketing_analytics_bi_reader;

COMMIT;
