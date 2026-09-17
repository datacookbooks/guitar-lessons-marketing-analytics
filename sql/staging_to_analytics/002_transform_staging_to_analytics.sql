BEGIN;

-- Every cast is guarded by CASE. PostgreSQL is not required to evaluate
-- Boolean AND terms left-to-right, so unsafe text is never placed in a cast
-- merely behind a regex predicate.

WITH cleaned AS (
    SELECT
        CASE
            WHEN BTRIM(plan_id) ~ '^[0-9]+$'
            THEN CASE
                WHEN BTRIM(plan_id)::numeric BETWEEN 1 AND 32767
                THEN BTRIM(plan_id)::numeric::smallint
            END
        END AS plan_id,
        CASE
            WHEN pg_input_is_valid(BTRIM(_generated_for_date), 'date')
            THEN BTRIM(_generated_for_date)::date
        END AS generated_for_date,
        NULLIF(BTRIM(plan_name), '') AS plan_name,
        CASE
            WHEN BTRIM(monthly_price) ~
                '^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)$'
            THEN CASE
                WHEN BTRIM(monthly_price)::numeric >= 0
                    AND BTRIM(monthly_price)::numeric < 100000000
                    AND BTRIM(monthly_price)::numeric =
                        ROUND(BTRIM(monthly_price)::numeric, 2)
                THEN BTRIM(monthly_price)::numeric(10, 2)
            END
        END AS monthly_price,
        CASE
            WHEN BTRIM(estimated_monthly_variable_cost) ~
                '^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)$'
            THEN CASE
                WHEN BTRIM(estimated_monthly_variable_cost)::numeric >= 0
                    AND BTRIM(estimated_monthly_variable_cost)::numeric < 100000000
                    AND BTRIM(estimated_monthly_variable_cost)::numeric =
                        ROUND(BTRIM(estimated_monthly_variable_cost)::numeric, 2)
                THEN BTRIM(estimated_monthly_variable_cost)::numeric(10, 2)
            END
        END AS estimated_monthly_variable_cost,
        CASE
            WHEN BTRIM(weekly_recorded_lesson_limit) ~
                '^[0-9]+([.]0+)?$'
            THEN CASE
                WHEN BTRIM(weekly_recorded_lesson_limit)::numeric
                    BETWEEN 0 AND 2147483647
                THEN BTRIM(weekly_recorded_lesson_limit)::numeric::integer
            END
        END AS weekly_recorded_lesson_limit,
        CASE
            WHEN BTRIM(private_sessions_per_month) ~ '^[0-9]+$'
            THEN CASE
                WHEN BTRIM(private_sessions_per_month)::numeric
                    BETWEEN 0 AND 2147483647
                THEN BTRIM(private_sessions_per_month)::numeric::integer
            END
        END AS private_sessions_per_month,
        CASE LOWER(BTRIM(is_paid_plan))
            WHEN 'true' THEN TRUE
            WHEN 'false' THEN FALSE
        END AS is_paid_plan,
        _raw_row_id AS selected_raw_row_id,
        source_s3_key,
        source_run_id,
        extracted_at,
        CASE
            WHEN _raw_row_id ~ '^[0-9]+$' THEN _raw_row_id::numeric
            ELSE -1
        END AS raw_delivery_order
    FROM staging.dim_plan
), ranked AS (
    SELECT
        cleaned.*,
        ROW_NUMBER() OVER (
            PARTITION BY plan_id
            ORDER BY
                generated_for_date DESC NULLS LAST,
                extracted_at DESC,
                raw_delivery_order DESC
        ) AS business_rank
    FROM cleaned
    WHERE plan_id IS NOT NULL
)
INSERT INTO analytics.dim_plan (
    plan_id,
    generated_for_date,
    plan_name,
    monthly_price,
    estimated_monthly_variable_cost,
    weekly_recorded_lesson_limit,
    private_sessions_per_month,
    is_paid_plan,
    selected_raw_row_id,
    source_s3_key,
    source_run_id
)
SELECT
    plan_id,
    generated_for_date,
    plan_name,
    monthly_price,
    estimated_monthly_variable_cost,
    weekly_recorded_lesson_limit,
    private_sessions_per_month,
    is_paid_plan,
    selected_raw_row_id,
    source_s3_key,
    source_run_id
FROM ranked
WHERE business_rank = 1
ON CONFLICT (plan_id) DO UPDATE
SET
    generated_for_date = EXCLUDED.generated_for_date,
    plan_name = EXCLUDED.plan_name,
    monthly_price = EXCLUDED.monthly_price,
    estimated_monthly_variable_cost =
        EXCLUDED.estimated_monthly_variable_cost,
    weekly_recorded_lesson_limit =
        EXCLUDED.weekly_recorded_lesson_limit,
    private_sessions_per_month = EXCLUDED.private_sessions_per_month,
    is_paid_plan = EXCLUDED.is_paid_plan,
    selected_raw_row_id = EXCLUDED.selected_raw_row_id,
    source_s3_key = EXCLUDED.source_s3_key,
    source_run_id = EXCLUDED.source_run_id,
    transformed_at = CURRENT_TIMESTAMP
WHERE (
    analytics.dim_plan.generated_for_date,
    analytics.dim_plan.plan_name,
    analytics.dim_plan.monthly_price,
    analytics.dim_plan.estimated_monthly_variable_cost,
    analytics.dim_plan.weekly_recorded_lesson_limit,
    analytics.dim_plan.private_sessions_per_month,
    analytics.dim_plan.is_paid_plan,
    analytics.dim_plan.selected_raw_row_id,
    analytics.dim_plan.source_s3_key,
    analytics.dim_plan.source_run_id
) IS DISTINCT FROM (
    EXCLUDED.generated_for_date,
    EXCLUDED.plan_name,
    EXCLUDED.monthly_price,
    EXCLUDED.estimated_monthly_variable_cost,
    EXCLUDED.weekly_recorded_lesson_limit,
    EXCLUDED.private_sessions_per_month,
    EXCLUDED.is_paid_plan,
    EXCLUDED.selected_raw_row_id,
    EXCLUDED.source_s3_key,
    EXCLUDED.source_run_id
);

WITH cleaned AS (
    SELECT
        CASE
            WHEN BTRIM(campaign_id) ~ '^[0-9]+$'
            THEN CASE
                WHEN BTRIM(campaign_id)::numeric BETWEEN 1 AND 2147483647
                THEN BTRIM(campaign_id)::numeric::integer
            END
        END AS campaign_id,
        CASE
            WHEN pg_input_is_valid(BTRIM(_generated_for_date), 'date')
            THEN BTRIM(_generated_for_date)::date
        END AS generated_for_date,
        NULLIF(BTRIM(campaign_name), '') AS campaign_name,
        CASE
            WHEN LOWER(BTRIM(channel)) IN (
                'email',
                'paid search',
                'paid social'
            )
            THEN LOWER(BTRIM(channel))
        END AS channel,
        CASE
            WHEN LOWER(BTRIM(objective)) IN (
                'acquisition',
                'free-to-paid conversion'
            )
            THEN LOWER(BTRIM(objective))
        END AS objective,
        CASE
            WHEN LOWER(BTRIM(primary_conversion_event)) IN (
                'registration',
                'first_paid_start'
            )
            THEN LOWER(BTRIM(primary_conversion_event))
            WHEN primary_conversion_event IS NULL
                AND LOWER(BTRIM(objective)) = 'acquisition'
            THEN 'registration'
            WHEN primary_conversion_event IS NULL
                AND LOWER(BTRIM(objective)) = 'free-to-paid conversion'
            THEN 'first_paid_start'
        END AS primary_conversion_event,
        CASE
            WHEN pg_input_is_valid(BTRIM(active_start_date), 'timestamp')
            THEN BTRIM(active_start_date)::timestamp::date
        END AS active_start_date,
        CASE
            WHEN pg_input_is_valid(BTRIM(active_end_date), 'timestamp')
            THEN BTRIM(active_end_date)::timestamp::date
        END AS active_end_date,
        CASE
            WHEN BTRIM(default_treatment_share) ~
                '^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)$'
            THEN CASE
                WHEN BTRIM(default_treatment_share)::numeric BETWEEN 0 AND 1
                THEN BTRIM(default_treatment_share)::numeric(5, 4)
            END
        END AS default_treatment_share,
        CASE LOWER(BTRIM(is_evergreen))
            WHEN 'true' THEN TRUE
            WHEN 'false' THEN FALSE
        END AS is_evergreen,
        _raw_row_id AS selected_raw_row_id,
        source_s3_key,
        source_run_id,
        extracted_at,
        CASE
            WHEN _raw_row_id ~ '^[0-9]+$' THEN _raw_row_id::numeric
            ELSE -1
        END AS raw_delivery_order
    FROM staging.dim_campaign
), ranked AS (
    SELECT
        cleaned.*,
        ROW_NUMBER() OVER (
            PARTITION BY campaign_id
            ORDER BY
                generated_for_date DESC NULLS LAST,
                extracted_at DESC,
                raw_delivery_order DESC
        ) AS business_rank
    FROM cleaned
    WHERE campaign_id IS NOT NULL
)
INSERT INTO analytics.dim_campaign (
    campaign_id,
    generated_for_date,
    campaign_name,
    channel,
    objective,
    primary_conversion_event,
    active_start_date,
    active_end_date,
    default_treatment_share,
    is_evergreen,
    selected_raw_row_id,
    source_s3_key,
    source_run_id
)
SELECT
    campaign_id,
    generated_for_date,
    campaign_name,
    channel,
    objective,
    primary_conversion_event,
    active_start_date,
    active_end_date,
    default_treatment_share,
    is_evergreen,
    selected_raw_row_id,
    source_s3_key,
    source_run_id
FROM ranked
WHERE business_rank = 1
ON CONFLICT (campaign_id) DO UPDATE
SET
    generated_for_date = EXCLUDED.generated_for_date,
    campaign_name = EXCLUDED.campaign_name,
    channel = EXCLUDED.channel,
    objective = EXCLUDED.objective,
    primary_conversion_event = EXCLUDED.primary_conversion_event,
    active_start_date = EXCLUDED.active_start_date,
    active_end_date = EXCLUDED.active_end_date,
    default_treatment_share = EXCLUDED.default_treatment_share,
    is_evergreen = EXCLUDED.is_evergreen,
    selected_raw_row_id = EXCLUDED.selected_raw_row_id,
    source_s3_key = EXCLUDED.source_s3_key,
    source_run_id = EXCLUDED.source_run_id,
    transformed_at = CURRENT_TIMESTAMP
WHERE analytics.dim_campaign.campaign_id <> -1
  AND (
    analytics.dim_campaign.generated_for_date,
    analytics.dim_campaign.campaign_name,
    analytics.dim_campaign.channel,
    analytics.dim_campaign.objective,
    analytics.dim_campaign.primary_conversion_event,
    analytics.dim_campaign.active_start_date,
    analytics.dim_campaign.active_end_date,
    analytics.dim_campaign.default_treatment_share,
    analytics.dim_campaign.is_evergreen,
    analytics.dim_campaign.selected_raw_row_id,
    analytics.dim_campaign.source_s3_key,
    analytics.dim_campaign.source_run_id
) IS DISTINCT FROM (
    EXCLUDED.generated_for_date,
    EXCLUDED.campaign_name,
    EXCLUDED.channel,
    EXCLUDED.objective,
    EXCLUDED.primary_conversion_event,
    EXCLUDED.active_start_date,
    EXCLUDED.active_end_date,
    EXCLUDED.default_treatment_share,
    EXCLUDED.is_evergreen,
    EXCLUDED.selected_raw_row_id,
    EXCLUDED.source_s3_key,
    EXCLUDED.source_run_id
);

WITH cleaned AS (
    SELECT
        CASE
            WHEN BTRIM(customer_id) ~ '^[0-9]+$'
            THEN CASE
                WHEN BTRIM(customer_id)::numeric
                    BETWEEN 1 AND 9223372036854775807
                THEN BTRIM(customer_id)::numeric::bigint
            END
        END AS customer_id,
        CASE
            WHEN pg_input_is_valid(BTRIM(_generated_for_date), 'date')
            THEN BTRIM(_generated_for_date)::date
        END AS generated_for_date,
        NULLIF(BTRIM(prospect_key), '') AS prospect_key,
        CASE
            WHEN LOWER(BTRIM(email)) ~
                '^[^[:space:]@]+@[^[:space:]@]+[.][^[:space:]@]+$'
            THEN LOWER(BTRIM(email))
        END AS email,
        CASE
            WHEN pg_input_is_valid(
                BTRIM(signup_timestamp),
                'timestamp with time zone'
            )
            THEN BTRIM(signup_timestamp)::timestamptz
        END AS signup_timestamp,
        CASE
            WHEN UPPER(BTRIM(state)) ~ '^[A-Z]{2}$'
            THEN UPPER(BTRIM(state))
        END AS state,
        CASE
            WHEN LOWER(BTRIM(experience_level)) IN (
                'beginner',
                'intermediate',
                'advanced'
            )
            THEN LOWER(BTRIM(experience_level))
        END AS experience_level,
        CASE
            WHEN LOWER(BTRIM(initial_acquisition_channel)) IN (
                'organic',
                'paid search',
                'paid social'
            )
            THEN LOWER(BTRIM(initial_acquisition_channel))
        END AS initial_acquisition_channel,
        CASE
            WHEN BTRIM(first_campaign_id) ~ '^[0-9]+([.]0+)?$'
            THEN CASE
                WHEN BTRIM(first_campaign_id)::numeric
                    BETWEEN 1 AND 2147483647
                THEN BTRIM(first_campaign_id)::numeric::integer
            END
        END AS source_first_campaign_id,
        CASE
            WHEN pg_input_is_valid(
                BTRIM(source_updated_at),
                'timestamp with time zone'
            )
            THEN BTRIM(source_updated_at)::timestamptz
        END AS source_updated_at,
        _raw_row_id AS selected_raw_row_id,
        source_s3_key,
        source_run_id,
        extracted_at,
        CASE
            WHEN _raw_row_id ~ '^[0-9]+$' THEN _raw_row_id::numeric
            ELSE -1
        END AS raw_delivery_order
    FROM staging.dim_customer
), ranked AS (
    SELECT
        cleaned.*,
        ROW_NUMBER() OVER (
            PARTITION BY customer_id
            ORDER BY
                source_updated_at DESC NULLS LAST,
                extracted_at DESC,
                raw_delivery_order DESC
        ) AS business_rank
    FROM cleaned
    WHERE customer_id IS NOT NULL
), selected AS (
    SELECT
        ranked.*,
        CASE
            WHEN source_first_campaign_id IS NULL THEN NULL
            WHEN EXISTS (
                SELECT 1
                FROM analytics.dim_campaign AS campaign
                WHERE campaign.campaign_id = ranked.source_first_campaign_id
            )
            THEN source_first_campaign_id
            ELSE -1
        END AS first_campaign_id
    FROM ranked
    WHERE business_rank = 1
)
INSERT INTO analytics.dim_customer (
    customer_id,
    generated_for_date,
    prospect_key,
    email,
    signup_timestamp,
    state,
    experience_level,
    initial_acquisition_channel,
    first_campaign_id,
    source_updated_at,
    selected_raw_row_id,
    source_s3_key,
    source_run_id
)
SELECT
    customer_id,
    generated_for_date,
    prospect_key,
    email,
    signup_timestamp,
    state,
    experience_level,
    initial_acquisition_channel,
    first_campaign_id,
    source_updated_at,
    selected_raw_row_id,
    source_s3_key,
    source_run_id
FROM selected
ON CONFLICT (customer_id) DO UPDATE
SET
    generated_for_date = EXCLUDED.generated_for_date,
    prospect_key = EXCLUDED.prospect_key,
    email = EXCLUDED.email,
    signup_timestamp = EXCLUDED.signup_timestamp,
    state = EXCLUDED.state,
    experience_level = EXCLUDED.experience_level,
    initial_acquisition_channel = EXCLUDED.initial_acquisition_channel,
    first_campaign_id = EXCLUDED.first_campaign_id,
    source_updated_at = EXCLUDED.source_updated_at,
    selected_raw_row_id = EXCLUDED.selected_raw_row_id,
    source_s3_key = EXCLUDED.source_s3_key,
    source_run_id = EXCLUDED.source_run_id,
    transformed_at = CURRENT_TIMESTAMP
WHERE (
    analytics.dim_customer.generated_for_date,
    analytics.dim_customer.prospect_key,
    analytics.dim_customer.email,
    analytics.dim_customer.signup_timestamp,
    analytics.dim_customer.state,
    analytics.dim_customer.experience_level,
    analytics.dim_customer.initial_acquisition_channel,
    analytics.dim_customer.first_campaign_id,
    analytics.dim_customer.source_updated_at,
    analytics.dim_customer.selected_raw_row_id,
    analytics.dim_customer.source_s3_key,
    analytics.dim_customer.source_run_id
) IS DISTINCT FROM (
    EXCLUDED.generated_for_date,
    EXCLUDED.prospect_key,
    EXCLUDED.email,
    EXCLUDED.signup_timestamp,
    EXCLUDED.state,
    EXCLUDED.experience_level,
    EXCLUDED.initial_acquisition_channel,
    EXCLUDED.first_campaign_id,
    EXCLUDED.source_updated_at,
    EXCLUDED.selected_raw_row_id,
    EXCLUDED.source_s3_key,
    EXCLUDED.source_run_id
);

WITH cleaned AS (
    SELECT
        NULLIF(BTRIM(subscription_period_id), '') AS subscription_period_id,
        CASE
            WHEN pg_input_is_valid(BTRIM(_generated_for_date), 'date')
            THEN BTRIM(_generated_for_date)::date
        END AS generated_for_date,
        CASE
            WHEN BTRIM(customer_id) ~ '^[0-9]+$'
            THEN CASE
                WHEN BTRIM(customer_id)::numeric
                    BETWEEN 1 AND 9223372036854775807
                THEN BTRIM(customer_id)::numeric::bigint
            END
        END AS source_customer_id,
        CASE
            WHEN BTRIM(plan_id) ~ '^[0-9]+$'
            THEN CASE
                WHEN BTRIM(plan_id)::numeric BETWEEN 1 AND 32767
                THEN BTRIM(plan_id)::numeric::smallint
            END
        END AS source_plan_id,
        CASE
            WHEN pg_input_is_valid(
                BTRIM(period_start_timestamp),
                'timestamp with time zone'
            )
            THEN BTRIM(period_start_timestamp)::timestamptz
        END AS period_start_timestamp,
        CASE
            WHEN pg_input_is_valid(
                BTRIM(period_end_timestamp),
                'timestamp with time zone'
            )
            THEN BTRIM(period_end_timestamp)::timestamptz
        END AS parsed_period_end_timestamp,
        CASE
            WHEN LOWER(BTRIM(end_reason)) IN (
                'cancellation',
                'downgrade',
                'move to free',
                'upgrade'
            )
            THEN LOWER(BTRIM(end_reason))
        END AS parsed_end_reason,
        CASE
            WHEN pg_input_is_valid(
                BTRIM(source_updated_at),
                'timestamp with time zone'
            )
            THEN BTRIM(source_updated_at)::timestamptz
        END AS source_updated_at,
        _raw_row_id AS selected_raw_row_id,
        source_s3_key,
        source_run_id,
        extracted_at,
        CASE
            WHEN _raw_row_id ~ '^[0-9]+$' THEN _raw_row_id::numeric
            ELSE -1
        END AS raw_delivery_order
    FROM staging.fact_subscription_period
), conformed AS (
    SELECT
        cleaned.*,
        CASE
            WHEN parsed_period_end_timestamp IS NULL
                AND parsed_end_reason IS NULL
            THEN NULL
            WHEN parsed_period_end_timestamp IS NOT NULL
                AND parsed_end_reason IS NOT NULL
            THEN parsed_period_end_timestamp
        END AS period_end_timestamp,
        CASE
            WHEN parsed_period_end_timestamp IS NULL
                AND parsed_end_reason IS NULL
            THEN NULL
            WHEN parsed_period_end_timestamp IS NOT NULL
                AND parsed_end_reason IS NOT NULL
            THEN parsed_end_reason
        END AS end_reason
    FROM cleaned
), ranked AS (
    SELECT
        conformed.*,
        ROW_NUMBER() OVER (
            PARTITION BY subscription_period_id
            ORDER BY
                source_updated_at DESC NULLS LAST,
                extracted_at DESC,
                raw_delivery_order DESC
        ) AS business_rank
    FROM conformed
    WHERE subscription_period_id IS NOT NULL
), selected AS (
    SELECT
        ranked.*,
        CASE
            WHEN EXISTS (
                SELECT 1
                FROM analytics.dim_customer AS customer
                WHERE customer.customer_id = ranked.source_customer_id
            )
            THEN source_customer_id
        END AS customer_id,
        CASE
            WHEN EXISTS (
                SELECT 1
                FROM analytics.dim_plan AS plan
                WHERE plan.plan_id = ranked.source_plan_id
            )
            THEN source_plan_id
        END AS plan_id
    FROM ranked
    WHERE business_rank = 1
)
INSERT INTO analytics.fact_subscription_period (
    subscription_period_id,
    generated_for_date,
    customer_id,
    plan_id,
    period_start_timestamp,
    period_end_timestamp,
    end_reason,
    source_updated_at,
    selected_raw_row_id,
    source_s3_key,
    source_run_id
)
SELECT
    subscription_period_id,
    generated_for_date,
    customer_id,
    plan_id,
    period_start_timestamp,
    period_end_timestamp,
    end_reason,
    source_updated_at,
    selected_raw_row_id,
    source_s3_key,
    source_run_id
FROM selected
ON CONFLICT (subscription_period_id) DO UPDATE
SET
    generated_for_date = EXCLUDED.generated_for_date,
    customer_id = EXCLUDED.customer_id,
    plan_id = EXCLUDED.plan_id,
    period_start_timestamp = EXCLUDED.period_start_timestamp,
    period_end_timestamp = EXCLUDED.period_end_timestamp,
    end_reason = EXCLUDED.end_reason,
    source_updated_at = EXCLUDED.source_updated_at,
    selected_raw_row_id = EXCLUDED.selected_raw_row_id,
    source_s3_key = EXCLUDED.source_s3_key,
    source_run_id = EXCLUDED.source_run_id,
    transformed_at = CURRENT_TIMESTAMP
WHERE (
    analytics.fact_subscription_period.generated_for_date,
    analytics.fact_subscription_period.customer_id,
    analytics.fact_subscription_period.plan_id,
    analytics.fact_subscription_period.period_start_timestamp,
    analytics.fact_subscription_period.period_end_timestamp,
    analytics.fact_subscription_period.end_reason,
    analytics.fact_subscription_period.source_updated_at,
    analytics.fact_subscription_period.selected_raw_row_id,
    analytics.fact_subscription_period.source_s3_key,
    analytics.fact_subscription_period.source_run_id
) IS DISTINCT FROM (
    EXCLUDED.generated_for_date,
    EXCLUDED.customer_id,
    EXCLUDED.plan_id,
    EXCLUDED.period_start_timestamp,
    EXCLUDED.period_end_timestamp,
    EXCLUDED.end_reason,
    EXCLUDED.source_updated_at,
    EXCLUDED.selected_raw_row_id,
    EXCLUDED.source_s3_key,
    EXCLUDED.source_run_id
);

WITH cleaned AS (
    SELECT
        NULLIF(BTRIM(payment_id), '') AS payment_id,
        CASE
            WHEN pg_input_is_valid(BTRIM(_generated_for_date), 'date')
            THEN BTRIM(_generated_for_date)::date
        END AS generated_for_date,
        NULLIF(BTRIM(subscription_period_id), '') AS source_subscription_period_id,
        CASE
            WHEN BTRIM(customer_id) ~ '^[0-9]+$'
            THEN CASE
                WHEN BTRIM(customer_id)::numeric
                    BETWEEN 1 AND 9223372036854775807
                THEN BTRIM(customer_id)::numeric::bigint
            END
        END AS source_customer_id,
        CASE
            WHEN pg_input_is_valid(
                BTRIM(payment_timestamp),
                'timestamp with time zone'
            )
            THEN BTRIM(payment_timestamp)::timestamptz
        END AS payment_timestamp,
        CASE
            WHEN BTRIM(amount) ~
                '^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)$'
            THEN CASE
                WHEN ABS(BTRIM(amount)::numeric) < 100000000
                    AND BTRIM(amount)::numeric = ROUND(BTRIM(amount)::numeric, 2)
                THEN BTRIM(amount)::numeric(10, 2)
            END
        END AS amount,
        CASE
            WHEN LOWER(BTRIM(payment_type)) IN (
                'initial',
                'renewal',
                'retry',
                'refund'
            )
            THEN LOWER(BTRIM(payment_type))
        END AS payment_type,
        CASE
            WHEN LOWER(BTRIM(payment_status)) IN ('failed', 'succeeded')
            THEN LOWER(BTRIM(payment_status))
        END AS payment_status,
        CASE
            WHEN BTRIM(attempt_number) ~ '^[0-9]+$'
            THEN CASE
                WHEN BTRIM(attempt_number)::numeric
                    BETWEEN 1 AND 2147483647
                THEN BTRIM(attempt_number)::numeric::integer
            END
        END AS attempt_number,
        CASE
            WHEN pg_input_is_valid(
                BTRIM(ingested_at),
                'timestamp with time zone'
            )
            THEN BTRIM(ingested_at)::timestamptz
        END AS ingested_at,
        _raw_row_id AS selected_raw_row_id,
        source_s3_key,
        source_run_id,
        extracted_at,
        CASE
            WHEN _raw_row_id ~ '^[0-9]+$' THEN _raw_row_id::numeric
            ELSE -1
        END AS raw_delivery_order
    FROM staging.fact_payment
), ranked AS (
    SELECT
        cleaned.*,
        ROW_NUMBER() OVER (
            PARTITION BY payment_id
            ORDER BY
                ingested_at DESC NULLS LAST,
                extracted_at DESC,
                raw_delivery_order DESC
        ) AS business_rank
    FROM cleaned
    WHERE payment_id IS NOT NULL
), selected AS (
    SELECT
        ranked.*,
        CASE
            WHEN EXISTS (
                SELECT 1
                FROM analytics.fact_subscription_period AS subscription
                WHERE subscription.subscription_period_id =
                    ranked.source_subscription_period_id
            )
            THEN source_subscription_period_id
        END AS subscription_period_id,
        CASE
            WHEN EXISTS (
                SELECT 1
                FROM analytics.dim_customer AS customer
                WHERE customer.customer_id = ranked.source_customer_id
            )
            THEN source_customer_id
        END AS customer_id
    FROM ranked
    WHERE business_rank = 1
)
INSERT INTO analytics.fact_payment (
    payment_id,
    generated_for_date,
    subscription_period_id,
    customer_id,
    payment_timestamp,
    amount,
    payment_type,
    payment_status,
    attempt_number,
    ingested_at,
    selected_raw_row_id,
    source_s3_key,
    source_run_id
)
SELECT
    payment_id,
    generated_for_date,
    subscription_period_id,
    customer_id,
    payment_timestamp,
    amount,
    payment_type,
    payment_status,
    attempt_number,
    ingested_at,
    selected_raw_row_id,
    source_s3_key,
    source_run_id
FROM selected
ON CONFLICT (payment_id) DO UPDATE
SET
    generated_for_date = EXCLUDED.generated_for_date,
    subscription_period_id = EXCLUDED.subscription_period_id,
    customer_id = EXCLUDED.customer_id,
    payment_timestamp = EXCLUDED.payment_timestamp,
    amount = EXCLUDED.amount,
    payment_type = EXCLUDED.payment_type,
    payment_status = EXCLUDED.payment_status,
    attempt_number = EXCLUDED.attempt_number,
    ingested_at = EXCLUDED.ingested_at,
    selected_raw_row_id = EXCLUDED.selected_raw_row_id,
    source_s3_key = EXCLUDED.source_s3_key,
    source_run_id = EXCLUDED.source_run_id,
    transformed_at = CURRENT_TIMESTAMP
WHERE (
    analytics.fact_payment.generated_for_date,
    analytics.fact_payment.subscription_period_id,
    analytics.fact_payment.customer_id,
    analytics.fact_payment.payment_timestamp,
    analytics.fact_payment.amount,
    analytics.fact_payment.payment_type,
    analytics.fact_payment.payment_status,
    analytics.fact_payment.attempt_number,
    analytics.fact_payment.ingested_at,
    analytics.fact_payment.selected_raw_row_id,
    analytics.fact_payment.source_s3_key,
    analytics.fact_payment.source_run_id
) IS DISTINCT FROM (
    EXCLUDED.generated_for_date,
    EXCLUDED.subscription_period_id,
    EXCLUDED.customer_id,
    EXCLUDED.payment_timestamp,
    EXCLUDED.amount,
    EXCLUDED.payment_type,
    EXCLUDED.payment_status,
    EXCLUDED.attempt_number,
    EXCLUDED.ingested_at,
    EXCLUDED.selected_raw_row_id,
    EXCLUDED.source_s3_key,
    EXCLUDED.source_run_id
);

WITH cleaned AS (
    SELECT
        CASE
            WHEN pg_input_is_valid(BTRIM(metric_date), 'timestamp')
            THEN BTRIM(metric_date)::timestamp::date
        END AS metric_date,
        CASE
            WHEN pg_input_is_valid(BTRIM(_generated_for_date), 'date')
            THEN BTRIM(_generated_for_date)::date
        END AS generated_for_date,
        CASE
            WHEN BTRIM(campaign_id) ~ '^[0-9]+$'
            THEN CASE
                WHEN BTRIM(campaign_id)::numeric BETWEEN 1 AND 2147483647
                THEN BTRIM(campaign_id)::numeric::integer
            END
        END AS source_campaign_id,
        CASE
            WHEN BTRIM(impressions) ~ '^[0-9]+$'
            THEN CASE
                WHEN BTRIM(impressions)::numeric
                    BETWEEN 0 AND 9223372036854775807
                THEN BTRIM(impressions)::numeric::bigint
            END
        END AS impressions,
        CASE
            WHEN BTRIM(clicks) ~ '^[0-9]+$'
            THEN CASE
                WHEN BTRIM(clicks)::numeric
                    BETWEEN 0 AND 9223372036854775807
                THEN BTRIM(clicks)::numeric::bigint
            END
        END AS clicks,
        CASE
            WHEN BTRIM(spend) ~
                '^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)$'
            THEN CASE
                WHEN BTRIM(spend)::numeric >= 0
                    AND BTRIM(spend)::numeric < 10000000000
                    AND BTRIM(spend)::numeric = ROUND(BTRIM(spend)::numeric, 2)
                THEN BTRIM(spend)::numeric(12, 2)
            END
        END AS spend,
        CASE
            WHEN BTRIM(platform_attributed_conversions) ~ '^[0-9]+$'
            THEN CASE
                WHEN BTRIM(platform_attributed_conversions)::numeric
                    BETWEEN 0 AND 9223372036854775807
                THEN BTRIM(platform_attributed_conversions)::numeric::bigint
            END
        END AS platform_attributed_conversions,
        CASE
            WHEN pg_input_is_valid(
                BTRIM(ingested_at),
                'timestamp with time zone'
            )
            THEN BTRIM(ingested_at)::timestamptz
        END AS ingested_at,
        _raw_row_id AS selected_raw_row_id,
        source_s3_key,
        source_run_id,
        extracted_at,
        CASE
            WHEN _raw_row_id ~ '^[0-9]+$' THEN _raw_row_id::numeric
            ELSE -1
        END AS raw_delivery_order
    FROM staging.fact_campaign_daily
), ranked AS (
    SELECT
        cleaned.*,
        ROW_NUMBER() OVER (
            PARTITION BY metric_date, source_campaign_id
            ORDER BY
                (
                    impressions IS NOT NULL
                    AND clicks IS NOT NULL
                    AND spend IS NOT NULL
                    AND platform_attributed_conversions IS NOT NULL
                    AND ingested_at IS NOT NULL
                ) DESC,
                generated_for_date DESC NULLS LAST,
                ingested_at DESC NULLS LAST,
                extracted_at DESC,
                raw_delivery_order DESC
        ) AS business_rank
    FROM cleaned
    WHERE metric_date IS NOT NULL
      AND source_campaign_id IS NOT NULL
), selected AS (
    SELECT
        ranked.*,
        CASE
            WHEN EXISTS (
                SELECT 1
                FROM analytics.dim_campaign AS campaign
                WHERE campaign.campaign_id = ranked.source_campaign_id
            )
            THEN source_campaign_id
            ELSE -1
        END AS campaign_id
    FROM ranked
    WHERE business_rank = 1
)
INSERT INTO analytics.fact_campaign_daily (
    metric_date,
    campaign_id,
    generated_for_date,
    impressions,
    clicks,
    spend,
    platform_attributed_conversions,
    ingested_at,
    selected_raw_row_id,
    source_s3_key,
    source_run_id
)
SELECT
    metric_date,
    campaign_id,
    generated_for_date,
    impressions,
    clicks,
    spend,
    platform_attributed_conversions,
    ingested_at,
    selected_raw_row_id,
    source_s3_key,
    source_run_id
FROM selected
ON CONFLICT (metric_date, campaign_id) DO UPDATE
SET
    generated_for_date = EXCLUDED.generated_for_date,
    impressions = EXCLUDED.impressions,
    clicks = EXCLUDED.clicks,
    spend = EXCLUDED.spend,
    platform_attributed_conversions =
        EXCLUDED.platform_attributed_conversions,
    ingested_at = EXCLUDED.ingested_at,
    selected_raw_row_id = EXCLUDED.selected_raw_row_id,
    source_s3_key = EXCLUDED.source_s3_key,
    source_run_id = EXCLUDED.source_run_id,
    transformed_at = CURRENT_TIMESTAMP
WHERE (
    analytics.fact_campaign_daily.generated_for_date,
    analytics.fact_campaign_daily.impressions,
    analytics.fact_campaign_daily.clicks,
    analytics.fact_campaign_daily.spend,
    analytics.fact_campaign_daily.platform_attributed_conversions,
    analytics.fact_campaign_daily.ingested_at,
    analytics.fact_campaign_daily.selected_raw_row_id,
    analytics.fact_campaign_daily.source_s3_key,
    analytics.fact_campaign_daily.source_run_id
) IS DISTINCT FROM (
    EXCLUDED.generated_for_date,
    EXCLUDED.impressions,
    EXCLUDED.clicks,
    EXCLUDED.spend,
    EXCLUDED.platform_attributed_conversions,
    EXCLUDED.ingested_at,
    EXCLUDED.selected_raw_row_id,
    EXCLUDED.source_s3_key,
    EXCLUDED.source_run_id
);

WITH cleaned AS (
    SELECT
        NULLIF(BTRIM(assignment_id), '') AS assignment_id,
        CASE
            WHEN pg_input_is_valid(BTRIM(_generated_for_date), 'date')
            THEN BTRIM(_generated_for_date)::date
        END AS generated_for_date,
        CASE
            WHEN BTRIM(campaign_id) ~ '^[0-9]+$'
            THEN CASE
                WHEN BTRIM(campaign_id)::numeric BETWEEN 1 AND 2147483647
                THEN BTRIM(campaign_id)::numeric::integer
            END
        END AS source_campaign_id,
        CASE
            WHEN BTRIM(measurement_window_id) ~ '^[0-9]{4}_Q[1-4]$'
            THEN BTRIM(measurement_window_id)
        END AS measurement_window_id,
        NULLIF(BTRIM(prospect_key), '') AS prospect_key,
        CASE
            WHEN BTRIM(customer_id) ~ '^[0-9]+([.]0+)?$'
            THEN CASE
                WHEN BTRIM(customer_id)::numeric
                    BETWEEN 1 AND 9223372036854775807
                THEN BTRIM(customer_id)::numeric::bigint
            END
        END AS source_customer_id,
        CASE
            WHEN LOWER(BTRIM(assignment_arm)) IN ('treatment', 'holdout')
            THEN LOWER(BTRIM(assignment_arm))
        END AS assignment_arm,
        CASE
            WHEN pg_input_is_valid(
                BTRIM(assigned_at),
                'timestamp with time zone'
            )
            THEN BTRIM(assigned_at)::timestamptz
        END AS assigned_at,
        CASE
            WHEN pg_input_is_valid(
                BTRIM(first_exposed_at),
                'timestamp with time zone'
            )
            THEN BTRIM(first_exposed_at)::timestamptz
        END AS parsed_first_exposed_at,
        CASE
            WHEN pg_input_is_valid(
                BTRIM(source_updated_at),
                'timestamp with time zone'
            )
            THEN BTRIM(source_updated_at)::timestamptz
        END AS source_updated_at,
        _raw_row_id AS selected_raw_row_id,
        source_s3_key,
        source_run_id,
        extracted_at,
        CASE
            WHEN _raw_row_id ~ '^[0-9]+$' THEN _raw_row_id::numeric
            ELSE -1
        END AS raw_delivery_order
    FROM staging.fact_campaign_assignment
), conformed AS (
    SELECT
        cleaned.*,
        CASE
            WHEN parsed_first_exposed_at IS NULL THEN NULL
            WHEN assigned_at IS NULL THEN parsed_first_exposed_at
            WHEN parsed_first_exposed_at >= assigned_at
            THEN parsed_first_exposed_at
        END AS first_exposed_at
    FROM cleaned
), ranked AS (
    SELECT
        conformed.*,
        ROW_NUMBER() OVER (
            PARTITION BY assignment_id
            ORDER BY
                source_updated_at DESC NULLS LAST,
                extracted_at DESC,
                raw_delivery_order DESC
        ) AS business_rank
    FROM conformed
    WHERE assignment_id IS NOT NULL
), selected AS (
    SELECT
        ranked.*,
        CASE
            WHEN EXISTS (
                SELECT 1
                FROM analytics.dim_campaign AS campaign
                WHERE campaign.campaign_id = ranked.source_campaign_id
            )
            THEN source_campaign_id
            ELSE -1
        END AS campaign_id,
        CASE
            WHEN EXISTS (
                SELECT 1
                FROM analytics.dim_customer AS customer
                WHERE customer.customer_id = ranked.source_customer_id
            )
            THEN source_customer_id
        END AS customer_id
    FROM ranked
    WHERE business_rank = 1
)
INSERT INTO analytics.fact_campaign_assignment (
    assignment_id,
    generated_for_date,
    campaign_id,
    measurement_window_id,
    prospect_key,
    customer_id,
    assignment_arm,
    assigned_at,
    first_exposed_at,
    source_updated_at,
    selected_raw_row_id,
    source_s3_key,
    source_run_id
)
SELECT
    assignment_id,
    generated_for_date,
    campaign_id,
    measurement_window_id,
    prospect_key,
    customer_id,
    assignment_arm,
    assigned_at,
    first_exposed_at,
    source_updated_at,
    selected_raw_row_id,
    source_s3_key,
    source_run_id
FROM selected
ON CONFLICT (assignment_id) DO UPDATE
SET
    generated_for_date = EXCLUDED.generated_for_date,
    campaign_id = EXCLUDED.campaign_id,
    measurement_window_id = EXCLUDED.measurement_window_id,
    prospect_key = EXCLUDED.prospect_key,
    customer_id = EXCLUDED.customer_id,
    assignment_arm = EXCLUDED.assignment_arm,
    assigned_at = EXCLUDED.assigned_at,
    first_exposed_at = EXCLUDED.first_exposed_at,
    source_updated_at = EXCLUDED.source_updated_at,
    selected_raw_row_id = EXCLUDED.selected_raw_row_id,
    source_s3_key = EXCLUDED.source_s3_key,
    source_run_id = EXCLUDED.source_run_id,
    transformed_at = CURRENT_TIMESTAMP
WHERE (
    analytics.fact_campaign_assignment.generated_for_date,
    analytics.fact_campaign_assignment.campaign_id,
    analytics.fact_campaign_assignment.measurement_window_id,
    analytics.fact_campaign_assignment.prospect_key,
    analytics.fact_campaign_assignment.customer_id,
    analytics.fact_campaign_assignment.assignment_arm,
    analytics.fact_campaign_assignment.assigned_at,
    analytics.fact_campaign_assignment.first_exposed_at,
    analytics.fact_campaign_assignment.source_updated_at,
    analytics.fact_campaign_assignment.selected_raw_row_id,
    analytics.fact_campaign_assignment.source_s3_key,
    analytics.fact_campaign_assignment.source_run_id
) IS DISTINCT FROM (
    EXCLUDED.generated_for_date,
    EXCLUDED.campaign_id,
    EXCLUDED.measurement_window_id,
    EXCLUDED.prospect_key,
    EXCLUDED.customer_id,
    EXCLUDED.assignment_arm,
    EXCLUDED.assigned_at,
    EXCLUDED.first_exposed_at,
    EXCLUDED.source_updated_at,
    EXCLUDED.selected_raw_row_id,
    EXCLUDED.source_s3_key,
    EXCLUDED.source_run_id
);

-- Source-value issues are retained separately from typed measures. The
-- selected-delivery flag is derived from the completed analytics upserts.
WITH issue_rows AS (
    SELECT
        'dim_customer'::text AS source_table,
        source._raw_row_id AS raw_row_id,
        COALESCE(BTRIM(source.customer_id), '<null>') AS business_key,
        'email'::text AS column_name,
        'missing_value'::text AS issue_code,
        NULL::text AS raw_value,
        EXISTS (
            SELECT 1
            FROM analytics.dim_customer AS target
            WHERE target.selected_raw_row_id = source._raw_row_id
        ) AS is_selected_delivery,
        source.source_s3_key,
        source.source_run_id
    FROM staging.dim_customer AS source
    WHERE source.email IS NULL

    UNION ALL

    SELECT
        'dim_customer',
        source._raw_row_id,
        COALESCE(BTRIM(source.customer_id), '<null>'),
        'email',
        'blank_value',
        source.email,
        EXISTS (
            SELECT 1
            FROM analytics.dim_customer AS target
            WHERE target.selected_raw_row_id = source._raw_row_id
        ),
        source.source_s3_key,
        source.source_run_id
    FROM staging.dim_customer AS source
    WHERE source.email IS NOT NULL
      AND BTRIM(source.email) = ''

    UNION ALL

    SELECT
        'fact_campaign_daily',
        source._raw_row_id,
        CONCAT_WS('|', BTRIM(source.metric_date), BTRIM(source.campaign_id)),
        'clicks',
        'blank_value',
        source.clicks,
        EXISTS (
            SELECT 1
            FROM analytics.fact_campaign_daily AS target
            WHERE target.selected_raw_row_id = source._raw_row_id
        ),
        source.source_s3_key,
        source.source_run_id
    FROM staging.fact_campaign_daily AS source
    WHERE source.clicks IS NOT NULL
      AND BTRIM(source.clicks) = ''

    UNION ALL

    SELECT
        'fact_payment',
        source._raw_row_id,
        COALESCE(BTRIM(source.payment_id), '<null>'),
        'amount',
        'invalid_numeric',
        source.amount,
        EXISTS (
            SELECT 1
            FROM analytics.fact_payment AS target
            WHERE target.selected_raw_row_id = source._raw_row_id
        ),
        source.source_s3_key,
        source.source_run_id
    FROM staging.fact_payment AS source
    WHERE NULLIF(BTRIM(source.amount), '') IS NOT NULL
      AND NOT (
        CASE
            WHEN BTRIM(source.amount) ~
                '^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)$'
            THEN ABS(BTRIM(source.amount)::numeric) < 100000000
                AND BTRIM(source.amount)::numeric =
                    ROUND(BTRIM(source.amount)::numeric, 2)
            ELSE FALSE
        END
      )

    UNION ALL

    SELECT
        'fact_campaign_daily',
        source._raw_row_id,
        CONCAT_WS('|', BTRIM(source.metric_date), BTRIM(source.campaign_id)),
        'spend',
        'invalid_numeric',
        source.spend,
        EXISTS (
            SELECT 1
            FROM analytics.fact_campaign_daily AS target
            WHERE target.selected_raw_row_id = source._raw_row_id
        ),
        source.source_s3_key,
        source.source_run_id
    FROM staging.fact_campaign_daily AS source
    WHERE NULLIF(BTRIM(source.spend), '') IS NOT NULL
      AND NOT (
        CASE
            WHEN BTRIM(source.spend) ~
                '^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)$'
            THEN BTRIM(source.spend)::numeric >= 0
                AND BTRIM(source.spend)::numeric < 10000000000
                AND BTRIM(source.spend)::numeric =
                    ROUND(BTRIM(source.spend)::numeric, 2)
            ELSE FALSE
        END
      )

    UNION ALL

    SELECT
        'fact_campaign_assignment',
        source._raw_row_id,
        COALESCE(BTRIM(source.assignment_id), '<null>'),
        'campaign_id',
        'unknown_reference',
        source.campaign_id,
        EXISTS (
            SELECT 1
            FROM analytics.fact_campaign_assignment AS target
            WHERE target.selected_raw_row_id = source._raw_row_id
        ),
        source.source_s3_key,
        source.source_run_id
    FROM staging.fact_campaign_assignment AS source
    WHERE BTRIM(source.campaign_id) ~ '^[0-9]+$'
      AND NOT EXISTS (
        SELECT 1
        FROM staging.dim_campaign AS campaign
        WHERE CASE
            WHEN BTRIM(campaign.campaign_id) ~ '^[0-9]+$'
            THEN BTRIM(campaign.campaign_id)::numeric
        END = CASE
            WHEN BTRIM(source.campaign_id) ~ '^[0-9]+$'
            THEN BTRIM(source.campaign_id)::numeric
        END
      )

    UNION ALL

    SELECT
        'fact_subscription_period',
        source._raw_row_id,
        COALESCE(BTRIM(source.subscription_period_id), '<null>'),
        'period_end_timestamp,end_reason',
        'inconsistent_null_pair',
        CONCAT_WS('|', source.period_end_timestamp, source.end_reason),
        EXISTS (
            SELECT 1
            FROM analytics.fact_subscription_period AS target
            WHERE target.selected_raw_row_id = source._raw_row_id
        ),
        source.source_s3_key,
        source.source_run_id
    FROM staging.fact_subscription_period AS source
    WHERE (NULLIF(BTRIM(source.period_end_timestamp), '') IS NULL)
        IS DISTINCT FROM (NULLIF(BTRIM(source.end_reason), '') IS NULL)
)
INSERT INTO analytics.data_quality_issue (
    source_table,
    raw_row_id,
    business_key,
    column_name,
    issue_code,
    raw_value,
    is_selected_delivery,
    source_s3_key,
    source_run_id
)
SELECT
    source_table,
    raw_row_id,
    business_key,
    column_name,
    issue_code,
    raw_value,
    is_selected_delivery,
    source_s3_key,
    source_run_id
FROM issue_rows
ON CONFLICT (source_table, raw_row_id, column_name, issue_code) DO UPDATE
SET
    business_key = EXCLUDED.business_key,
    raw_value = EXCLUDED.raw_value,
    is_selected_delivery = EXCLUDED.is_selected_delivery,
    source_s3_key = EXCLUDED.source_s3_key,
    source_run_id = EXCLUDED.source_run_id,
    detected_at = CURRENT_TIMESTAMP
WHERE (
    analytics.data_quality_issue.business_key,
    analytics.data_quality_issue.raw_value,
    analytics.data_quality_issue.is_selected_delivery,
    analytics.data_quality_issue.source_s3_key,
    analytics.data_quality_issue.source_run_id
) IS DISTINCT FROM (
    EXCLUDED.business_key,
    EXCLUDED.raw_value,
    EXCLUDED.is_selected_delivery,
    EXCLUDED.source_s3_key,
    EXCLUDED.source_run_id
);

WITH exact_ranked AS (
    SELECT
        'dim_plan'::text AS source_table,
        _raw_row_id AS raw_row_id,
        COALESCE(BTRIM(plan_id), '<null>') AS business_key,
        source_s3_key,
        source_run_id,
        ROW_NUMBER() OVER (
            PARTITION BY
                _generated_for_date,
                plan_id,
                plan_name,
                monthly_price,
                estimated_monthly_variable_cost,
                weekly_recorded_lesson_limit,
                private_sessions_per_month,
                is_paid_plan
            ORDER BY
                extracted_at DESC,
                CASE
                    WHEN _raw_row_id ~ '^[0-9]+$' THEN _raw_row_id::numeric
                    ELSE -1
                END DESC
        ) AS exact_rank
    FROM staging.dim_plan

    UNION ALL

    SELECT
        'dim_campaign',
        _raw_row_id,
        COALESCE(BTRIM(campaign_id), '<null>'),
        source_s3_key,
        source_run_id,
        ROW_NUMBER() OVER (
            PARTITION BY
                _generated_for_date,
                campaign_id,
                campaign_name,
                channel,
                objective,
                active_start_date,
                active_end_date,
                default_treatment_share,
                is_evergreen
            ORDER BY
                extracted_at DESC,
                CASE
                    WHEN _raw_row_id ~ '^[0-9]+$' THEN _raw_row_id::numeric
                    ELSE -1
                END DESC
        )
    FROM staging.dim_campaign

    UNION ALL

    SELECT
        'dim_customer',
        _raw_row_id,
        COALESCE(BTRIM(customer_id), '<null>'),
        source_s3_key,
        source_run_id,
        ROW_NUMBER() OVER (
            PARTITION BY
                _generated_for_date,
                customer_id,
                prospect_key,
                email,
                signup_timestamp,
                state,
                experience_level,
                initial_acquisition_channel,
                first_campaign_id,
                source_updated_at
            ORDER BY
                extracted_at DESC,
                CASE
                    WHEN _raw_row_id ~ '^[0-9]+$' THEN _raw_row_id::numeric
                    ELSE -1
                END DESC
        )
    FROM staging.dim_customer

    UNION ALL

    SELECT
        'fact_subscription_period',
        _raw_row_id,
        COALESCE(BTRIM(subscription_period_id), '<null>'),
        source_s3_key,
        source_run_id,
        ROW_NUMBER() OVER (
            PARTITION BY
                _generated_for_date,
                subscription_period_id,
                customer_id,
                plan_id,
                period_start_timestamp,
                period_end_timestamp,
                end_reason,
                source_updated_at
            ORDER BY
                extracted_at DESC,
                CASE
                    WHEN _raw_row_id ~ '^[0-9]+$' THEN _raw_row_id::numeric
                    ELSE -1
                END DESC
        )
    FROM staging.fact_subscription_period

    UNION ALL

    SELECT
        'fact_payment',
        _raw_row_id,
        COALESCE(BTRIM(payment_id), '<null>'),
        source_s3_key,
        source_run_id,
        ROW_NUMBER() OVER (
            PARTITION BY
                _generated_for_date,
                payment_id,
                subscription_period_id,
                customer_id,
                payment_timestamp,
                amount,
                payment_type,
                payment_status,
                attempt_number,
                ingested_at
            ORDER BY
                extracted_at DESC,
                CASE
                    WHEN _raw_row_id ~ '^[0-9]+$' THEN _raw_row_id::numeric
                    ELSE -1
                END DESC
        )
    FROM staging.fact_payment

    UNION ALL

    SELECT
        'fact_campaign_daily',
        _raw_row_id,
        CONCAT_WS('|', BTRIM(metric_date), BTRIM(campaign_id)),
        source_s3_key,
        source_run_id,
        ROW_NUMBER() OVER (
            PARTITION BY
                _generated_for_date,
                metric_date,
                campaign_id,
                impressions,
                clicks,
                spend,
                platform_attributed_conversions,
                ingested_at
            ORDER BY
                extracted_at DESC,
                CASE
                    WHEN _raw_row_id ~ '^[0-9]+$' THEN _raw_row_id::numeric
                    ELSE -1
                END DESC
        )
    FROM staging.fact_campaign_daily

    UNION ALL

    SELECT
        'fact_campaign_assignment',
        _raw_row_id,
        COALESCE(BTRIM(assignment_id), '<null>'),
        source_s3_key,
        source_run_id,
        ROW_NUMBER() OVER (
            PARTITION BY
                _generated_for_date,
                assignment_id,
                campaign_id,
                measurement_window_id,
                prospect_key,
                customer_id,
                assignment_arm,
                assigned_at,
                first_exposed_at,
                source_updated_at
            ORDER BY
                extracted_at DESC,
                CASE
                    WHEN _raw_row_id ~ '^[0-9]+$' THEN _raw_row_id::numeric
                    ELSE -1
                END DESC
        )
    FROM staging.fact_campaign_assignment
)
INSERT INTO analytics.data_quality_issue (
    source_table,
    raw_row_id,
    business_key,
    column_name,
    issue_code,
    raw_value,
    is_selected_delivery,
    source_s3_key,
    source_run_id
)
SELECT
    source_table,
    raw_row_id,
    business_key,
    '__row__',
    'exact_duplicate',
    NULL,
    FALSE,
    source_s3_key,
    source_run_id
FROM exact_ranked
WHERE exact_rank > 1
ON CONFLICT (source_table, raw_row_id, column_name, issue_code) DO UPDATE
SET
    business_key = EXCLUDED.business_key,
    is_selected_delivery = FALSE,
    source_s3_key = EXCLUDED.source_s3_key,
    source_run_id = EXCLUDED.source_run_id,
    detected_at = CURRENT_TIMESTAMP
WHERE (
    analytics.data_quality_issue.business_key,
    analytics.data_quality_issue.is_selected_delivery,
    analytics.data_quality_issue.source_s3_key,
    analytics.data_quality_issue.source_run_id
) IS DISTINCT FROM (
    EXCLUDED.business_key,
    FALSE,
    EXCLUDED.source_s3_key,
    EXCLUDED.source_run_id
);

WITH superseded AS (
    SELECT
        'dim_plan'::text AS source_table,
        source._raw_row_id AS raw_row_id,
        COALESCE(BTRIM(source.plan_id), '<null>') AS business_key,
        source.source_s3_key,
        source.source_run_id
    FROM staging.dim_plan AS source
    JOIN analytics.dim_plan AS target
      ON CASE
            WHEN BTRIM(source.plan_id) ~ '^[0-9]+$'
            THEN BTRIM(source.plan_id)::numeric
         END = target.plan_id
    WHERE source._raw_row_id <> target.selected_raw_row_id

    UNION ALL

    SELECT
        'dim_campaign',
        source._raw_row_id,
        COALESCE(BTRIM(source.campaign_id), '<null>'),
        source.source_s3_key,
        source.source_run_id
    FROM staging.dim_campaign AS source
    JOIN analytics.dim_campaign AS target
      ON CASE
            WHEN BTRIM(source.campaign_id) ~ '^[0-9]+$'
            THEN BTRIM(source.campaign_id)::numeric
         END = target.campaign_id
    WHERE source._raw_row_id <> target.selected_raw_row_id

    UNION ALL

    SELECT
        'dim_customer',
        source._raw_row_id,
        COALESCE(BTRIM(source.customer_id), '<null>'),
        source.source_s3_key,
        source.source_run_id
    FROM staging.dim_customer AS source
    JOIN analytics.dim_customer AS target
      ON CASE
            WHEN BTRIM(source.customer_id) ~ '^[0-9]+$'
            THEN BTRIM(source.customer_id)::numeric
         END = target.customer_id
    WHERE source._raw_row_id <> target.selected_raw_row_id

    UNION ALL

    SELECT
        'fact_subscription_period',
        source._raw_row_id,
        COALESCE(BTRIM(source.subscription_period_id), '<null>'),
        source.source_s3_key,
        source.source_run_id
    FROM staging.fact_subscription_period AS source
    JOIN analytics.fact_subscription_period AS target
      ON BTRIM(source.subscription_period_id) = target.subscription_period_id
    WHERE source._raw_row_id <> target.selected_raw_row_id

    UNION ALL

    SELECT
        'fact_payment',
        source._raw_row_id,
        COALESCE(BTRIM(source.payment_id), '<null>'),
        source.source_s3_key,
        source.source_run_id
    FROM staging.fact_payment AS source
    JOIN analytics.fact_payment AS target
      ON BTRIM(source.payment_id) = target.payment_id
    WHERE source._raw_row_id <> target.selected_raw_row_id

    UNION ALL

    SELECT
        'fact_campaign_daily',
        source._raw_row_id,
        CONCAT_WS('|', BTRIM(source.metric_date), BTRIM(source.campaign_id)),
        source.source_s3_key,
        source.source_run_id
    FROM staging.fact_campaign_daily AS source
    JOIN analytics.fact_campaign_daily AS target
      ON CASE
            WHEN pg_input_is_valid(BTRIM(source.metric_date), 'timestamp')
            THEN BTRIM(source.metric_date)::timestamp::date
         END = target.metric_date
     AND CASE
            WHEN BTRIM(source.campaign_id) ~ '^[0-9]+$'
            THEN BTRIM(source.campaign_id)::numeric
         END = target.campaign_id
    WHERE source._raw_row_id <> target.selected_raw_row_id

    UNION ALL

    SELECT
        'fact_campaign_assignment',
        source._raw_row_id,
        COALESCE(BTRIM(source.assignment_id), '<null>'),
        source.source_s3_key,
        source.source_run_id
    FROM staging.fact_campaign_assignment AS source
    JOIN analytics.fact_campaign_assignment AS target
      ON BTRIM(source.assignment_id) = target.assignment_id
    WHERE source._raw_row_id <> target.selected_raw_row_id
)
INSERT INTO analytics.data_quality_issue (
    source_table,
    raw_row_id,
    business_key,
    column_name,
    issue_code,
    raw_value,
    is_selected_delivery,
    source_s3_key,
    source_run_id
)
SELECT
    source_table,
    raw_row_id,
    business_key,
    '__row__',
    'superseded_delivery',
    NULL,
    FALSE,
    source_s3_key,
    source_run_id
FROM superseded
ON CONFLICT (source_table, raw_row_id, column_name, issue_code) DO UPDATE
SET
    business_key = EXCLUDED.business_key,
    is_selected_delivery = FALSE,
    source_s3_key = EXCLUDED.source_s3_key,
    source_run_id = EXCLUDED.source_run_id,
    detected_at = CURRENT_TIMESTAMP
WHERE (
    analytics.data_quality_issue.business_key,
    analytics.data_quality_issue.is_selected_delivery,
    analytics.data_quality_issue.source_s3_key,
    analytics.data_quality_issue.source_run_id
) IS DISTINCT FROM (
    EXCLUDED.business_key,
    FALSE,
    EXCLUDED.source_s3_key,
    EXCLUDED.source_run_id
);

COMMIT;
