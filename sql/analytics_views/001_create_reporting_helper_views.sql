BEGIN;

-- Singleton cutoff shared by reporting helpers. Cross-subject metrics stop at
-- the earliest latest date among their required analytics sources.
CREATE OR REPLACE VIEW analytics.vw_reporting_cutoff AS
WITH source_maxima AS (
    SELECT MAX(generated_for_date) AS maximum_date
    FROM analytics.dim_customer
    UNION ALL
    SELECT MAX(generated_for_date)
    FROM analytics.fact_subscription_period
    UNION ALL
    SELECT MAX(generated_for_date)
    FROM analytics.fact_payment
    UNION ALL
    SELECT MAX(generated_for_date)
    FROM analytics.fact_campaign_daily
    UNION ALL
    SELECT MAX(generated_for_date)
    FROM analytics.fact_campaign_assignment
), cutoff AS (
    SELECT MIN(maximum_date) AS data_through_date
    FROM source_maxima
)
SELECT
    data_through_date,
    (data_through_date + 1)::timestamp AT TIME ZONE 'UTC'
        AS data_through_exclusive
FROM cutoff;

-- Grain: one row per customer who has ever entered a paid plan.
CREATE OR REPLACE VIEW analytics.vw_customer_paid_cohort AS
WITH ranked_paid_starts AS (
    SELECT
        period.customer_id,
        period.subscription_period_id,
        period.plan_id,
        period.period_start_timestamp AS first_paid_at,
        ROW_NUMBER() OVER (
            PARTITION BY period.customer_id
            ORDER BY
                period.period_start_timestamp,
                period.subscription_period_id
        ) AS paid_start_rank
    FROM analytics.fact_subscription_period AS period
    JOIN analytics.dim_plan AS plan
      ON plan.plan_id = period.plan_id
     AND plan.is_paid_plan
)
SELECT
    ranked.customer_id,
    ranked.subscription_period_id AS first_paid_subscription_period_id,
    ranked.plan_id AS initial_paid_plan_id,
    ranked.first_paid_at,
    DATE_TRUNC('month', ranked.first_paid_at)::date AS paid_cohort_month,
    customer.signup_timestamp,
    customer.initial_acquisition_channel,
    customer.first_campaign_id,
    customer.state,
    customer.experience_level
FROM ranked_paid_starts AS ranked
JOIN analytics.dim_customer AS customer
  ON customer.customer_id = ranked.customer_id
WHERE ranked.paid_start_rank = 1;

-- Grain: one row per paid-cohort customer and calendar month from first paid
-- entry through the reporting cutoff month.
CREATE OR REPLACE VIEW analytics.vw_customer_monthly_subscription_state AS
WITH cutoff AS (
    SELECT data_through_date, data_through_exclusive
    FROM analytics.vw_reporting_cutoff
), customer_months AS (
    SELECT
        cohort.*,
        month_start::date AS month_start,
        (month_start + INTERVAL '1 month')::date AS next_month_start
    FROM analytics.vw_customer_paid_cohort AS cohort
    CROSS JOIN cutoff
    CROSS JOIN LATERAL GENERATE_SERIES(
        DATE_TRUNC('month', cohort.first_paid_at),
        DATE_TRUNC('month', cutoff.data_through_date::timestamp),
        INTERVAL '1 month'
    ) AS generated_month(month_start)
), reactivation_events AS (
    SELECT
        sequenced.customer_id,
        sequenced.period_start_timestamp AS reactivated_at
    FROM (
        SELECT
            period.customer_id,
            period.plan_id,
            period.period_start_timestamp,
            LAG(period.period_end_timestamp) OVER (
                PARTITION BY period.customer_id
                ORDER BY
                    period.period_start_timestamp,
                    period.subscription_period_id
            ) AS previous_end_timestamp,
            LAG(period.end_reason) OVER (
                PARTITION BY period.customer_id
                ORDER BY
                    period.period_start_timestamp,
                    period.subscription_period_id
            ) AS previous_end_reason
        FROM analytics.fact_subscription_period AS period
    ) AS sequenced
    JOIN analytics.dim_plan AS plan
      ON plan.plan_id = sequenced.plan_id
     AND plan.is_paid_plan
    WHERE sequenced.previous_end_reason = 'cancellation'
      AND sequenced.period_start_timestamp
            > sequenced.previous_end_timestamp
)
SELECT
    customer_months.customer_id,
    customer_months.month_start,
    customer_months.next_month_start,
    customer_months.paid_cohort_month,
    customer_months.first_paid_at,
    customer_months.initial_paid_plan_id,
    customer_months.initial_acquisition_channel,
    customer_months.first_campaign_id,
    customer_months.state,
    customer_months.experience_level,
    opening.subscription_period_id AS opening_subscription_period_id,
    opening.plan_id AS opening_plan_id,
    (opening.subscription_period_id IS NOT NULL) AS is_opening_paid_customer,
    closing.plan_id AS closing_plan_id,
    (closing.subscription_period_id IS NOT NULL) AS is_closing_paid_customer,
    (
        opening.subscription_period_id IS NOT NULL
        AND EXISTS (
            SELECT 1
            FROM analytics.fact_subscription_period AS churn_period
            JOIN analytics.dim_plan AS churn_plan
              ON churn_plan.plan_id = churn_period.plan_id
             AND churn_plan.is_paid_plan
            WHERE churn_period.customer_id = customer_months.customer_id
              AND churn_period.period_end_timestamp
                    >= customer_months.month_start::timestamp AT TIME ZONE 'UTC'
              AND churn_period.period_end_timestamp
                    < customer_months.next_month_start::timestamp AT TIME ZONE 'UTC'
              AND churn_period.end_reason IN ('cancellation', 'move to free')
        )
    ) AS churned_opening_customer,
    COALESCE(movement.upgrade_events, 0) AS upgrade_events,
    COALESCE(movement.downgrade_events, 0) AS downgrade_events,
    COALESCE(reactivation.reactivation_events, 0) AS reactivation_events,
    (
        customer_months.next_month_start::timestamp AT TIME ZONE 'UTC'
        <= cutoff.data_through_exclusive
    ) AS is_complete_month,
    (
        EXTRACT(YEAR FROM AGE(
            customer_months.month_start,
            customer_months.paid_cohort_month
        ))::integer * 12
        + EXTRACT(MONTH FROM AGE(
            customer_months.month_start,
            customer_months.paid_cohort_month
        ))::integer
    ) AS paid_tenure_month_number,
    CASE
        WHEN customer_months.month_start
                < customer_months.paid_cohort_month + INTERVAL '3 months'
            THEN '0-2 months'
        WHEN customer_months.month_start
                < customer_months.paid_cohort_month + INTERVAL '6 months'
            THEN '3-5 months'
        WHEN customer_months.month_start
                < customer_months.paid_cohort_month + INTERVAL '12 months'
            THEN '6-11 months'
        ELSE '12+ months'
    END AS paid_tenure_band
FROM customer_months
CROSS JOIN cutoff
LEFT JOIN LATERAL (
    SELECT
        period.subscription_period_id,
        period.plan_id
    FROM analytics.fact_subscription_period AS period
    JOIN analytics.dim_plan AS plan
      ON plan.plan_id = period.plan_id
     AND plan.is_paid_plan
    WHERE period.customer_id = customer_months.customer_id
      AND period.period_start_timestamp
            <= customer_months.month_start::timestamp AT TIME ZONE 'UTC'
      AND (
            period.period_end_timestamp IS NULL
            OR period.period_end_timestamp
                > customer_months.month_start::timestamp AT TIME ZONE 'UTC'
          )
    ORDER BY period.period_start_timestamp DESC, period.subscription_period_id
    LIMIT 1
) AS opening ON TRUE
LEFT JOIN LATERAL (
    SELECT
        period.subscription_period_id,
        period.plan_id
    FROM analytics.fact_subscription_period AS period
    JOIN analytics.dim_plan AS plan
      ON plan.plan_id = period.plan_id
     AND plan.is_paid_plan
    WHERE period.customer_id = customer_months.customer_id
      AND period.period_start_timestamp
            < LEAST(
                customer_months.next_month_start::timestamp AT TIME ZONE 'UTC',
                cutoff.data_through_exclusive
              )
      AND (
            period.period_end_timestamp IS NULL
            OR period.period_end_timestamp
                >= LEAST(
                    customer_months.next_month_start::timestamp AT TIME ZONE 'UTC',
                    cutoff.data_through_exclusive
                )
          )
    ORDER BY period.period_start_timestamp DESC, period.subscription_period_id
    LIMIT 1
) AS closing ON TRUE
LEFT JOIN LATERAL (
    SELECT
        COUNT(*) FILTER (WHERE period.end_reason = 'upgrade') AS upgrade_events,
        COUNT(*) FILTER (WHERE period.end_reason = 'downgrade')
            AS downgrade_events
    FROM analytics.fact_subscription_period AS period
    JOIN analytics.dim_plan AS plan
      ON plan.plan_id = period.plan_id
     AND plan.is_paid_plan
    WHERE period.customer_id = customer_months.customer_id
      AND period.period_end_timestamp
            >= customer_months.month_start::timestamp AT TIME ZONE 'UTC'
      AND period.period_end_timestamp
            < customer_months.next_month_start::timestamp AT TIME ZONE 'UTC'
) AS movement ON TRUE
LEFT JOIN LATERAL (
    SELECT COUNT(*) AS reactivation_events
    FROM reactivation_events AS event
    WHERE event.customer_id = customer_months.customer_id
      AND event.reactivated_at
            >= customer_months.month_start::timestamp AT TIME ZONE 'UTC'
      AND event.reactivated_at
            < customer_months.next_month_start::timestamp AT TIME ZONE 'UTC'
) AS reactivation ON TRUE;

-- Grain: one row per customer and calendar month with subscription service or
-- a succeeded payment event. Payment and cost facts are reduced independently
-- before their compatible customer-month join.
CREATE OR REPLACE VIEW analytics.vw_customer_monthly_contribution AS
WITH cutoff AS (
    SELECT data_through_exclusive
    FROM analytics.vw_reporting_cutoff
), service_cost AS (
    SELECT
        period.customer_id,
        month_start::date AS month_start,
        ROUND(
            SUM(
                plan.estimated_monthly_variable_cost
                * EXTRACT(EPOCH FROM (
                    LEAST(
                        COALESCE(
                            period.period_end_timestamp,
                            cutoff.data_through_exclusive
                        ),
                        month_start + INTERVAL '1 month',
                        cutoff.data_through_exclusive
                    )
                    - GREATEST(period.period_start_timestamp, month_start)
                ))::numeric
                / NULLIF(
                    EXTRACT(EPOCH FROM (
                        month_start + INTERVAL '1 month' - month_start
                    ))::numeric,
                    0
                )
            ),
            2
        ) AS prorated_service_cost
    FROM analytics.fact_subscription_period AS period
    JOIN analytics.dim_plan AS plan
      ON plan.plan_id = period.plan_id
    CROSS JOIN cutoff
    CROSS JOIN LATERAL GENERATE_SERIES(
        DATE_TRUNC('month', period.period_start_timestamp),
        DATE_TRUNC(
            'month',
            LEAST(
                COALESCE(
                    period.period_end_timestamp,
                    cutoff.data_through_exclusive
                ),
                cutoff.data_through_exclusive
            ) - INTERVAL '1 microsecond'
        ),
        INTERVAL '1 month'
    ) AS generated_month(month_start)
    WHERE period.period_start_timestamp < cutoff.data_through_exclusive
      AND COALESCE(
            period.period_end_timestamp,
            cutoff.data_through_exclusive
          ) > period.period_start_timestamp
    GROUP BY period.customer_id, month_start::date
), succeeded_payment AS (
    SELECT
        payment.payment_id,
        payment.customer_id,
        DATE_TRUNC('month', payment.payment_timestamp)::date AS month_start,
        payment.payment_type,
        payment.amount AS reported_amount,
        CASE
            WHEN payment.amount IS NOT NULL THEN payment.amount
            WHEN payment.payment_type = 'refund' THEN -plan.monthly_price
            WHEN payment.payment_type IN ('initial', 'renewal', 'retry')
                THEN plan.monthly_price
        END AS effective_amount,
        (payment.amount IS NULL) AS is_amount_imputed
    FROM analytics.fact_payment AS payment
    JOIN analytics.fact_subscription_period AS period
      ON period.subscription_period_id = payment.subscription_period_id
    JOIN analytics.dim_plan AS plan
      ON plan.plan_id = period.plan_id
    WHERE payment.payment_status = 'succeeded'
), payment_month AS (
    SELECT
        customer_id,
        month_start,
        SUM(reported_amount) AS reported_net_revenue,
        SUM(effective_amount) FILTER (
            WHERE payment_type IN ('initial', 'renewal', 'retry')
        ) AS effective_charge_revenue,
        SUM(effective_amount) FILTER (WHERE payment_type = 'refund')
            AS effective_refund_amount,
        SUM(effective_amount) AS effective_net_revenue,
        COUNT(*) FILTER (WHERE is_amount_imputed) AS imputed_amount_events,
        COUNT(*) AS succeeded_payment_events
    FROM succeeded_payment
    GROUP BY customer_id, month_start
)
SELECT
    COALESCE(payment_month.customer_id, service_cost.customer_id) AS customer_id,
    COALESCE(payment_month.month_start, service_cost.month_start) AS month_start,
    COALESCE(payment_month.reported_net_revenue, 0) AS reported_net_revenue,
    COALESCE(payment_month.effective_charge_revenue, 0)
        AS effective_charge_revenue,
    COALESCE(payment_month.effective_refund_amount, 0)
        AS effective_refund_amount,
    COALESCE(payment_month.effective_net_revenue, 0) AS effective_net_revenue,
    COALESCE(service_cost.prorated_service_cost, 0) AS prorated_service_cost,
    COALESCE(payment_month.effective_net_revenue, 0)
        - COALESCE(service_cost.prorated_service_cost, 0)
        AS realized_contribution,
    COALESCE(payment_month.imputed_amount_events, 0) AS imputed_amount_events,
    COALESCE(payment_month.succeeded_payment_events, 0)
        AS succeeded_payment_events
FROM payment_month
FULL OUTER JOIN service_cost
  ON service_cost.customer_id = payment_month.customer_id
 AND service_cost.month_start = payment_month.month_start;

-- Grain: one row per paid-cohort customer and exact paid anniversary 1-12.
CREATE OR REPLACE VIEW analytics.vw_customer_clv_month AS
WITH checkpoints AS (
    SELECT GENERATE_SERIES(1, 12) AS month_number
), eligible_rows AS (
    SELECT
        cohort.*,
        checkpoints.month_number,
        cohort.first_paid_at
            + checkpoints.month_number * INTERVAL '1 month'
            AS checkpoint_timestamp,
        cutoff.data_through_exclusive
    FROM analytics.vw_customer_paid_cohort AS cohort
    CROSS JOIN checkpoints
    CROSS JOIN analytics.vw_reporting_cutoff AS cutoff
)
SELECT
    eligible.customer_id,
    eligible.paid_cohort_month,
    eligible.first_paid_at,
    eligible.initial_paid_plan_id,
    eligible.initial_acquisition_channel,
    eligible.first_campaign_id,
    eligible.state,
    eligible.experience_level,
    eligible.month_number,
    eligible.checkpoint_timestamp,
    (eligible.checkpoint_timestamp <= eligible.data_through_exclusive)
        AS is_checkpoint_mature,
    active.plan_id AS active_paid_plan_id,
    (active.plan_id IS NOT NULL) AS is_retained_paid,
    CASE
        WHEN active.plan_id IS NOT NULL
            THEN active.monthly_price - active.estimated_monthly_variable_cost
    END AS conditional_standard_contribution_margin
FROM eligible_rows AS eligible
LEFT JOIN LATERAL (
    SELECT
        plan.plan_id,
        plan.monthly_price,
        plan.estimated_monthly_variable_cost
    FROM analytics.fact_subscription_period AS period
    JOIN analytics.dim_plan AS plan
      ON plan.plan_id = period.plan_id
     AND plan.is_paid_plan
    WHERE period.customer_id = eligible.customer_id
      AND period.period_start_timestamp <= eligible.checkpoint_timestamp
      AND (
            period.period_end_timestamp IS NULL
            OR period.period_end_timestamp > eligible.checkpoint_timestamp
          )
    ORDER BY period.period_start_timestamp DESC, period.subscription_period_id
    LIMIT 1
) AS active ON TRUE;

-- Grain: one billing episode containing at least one failed charge attempt.
CREATE OR REPLACE VIEW analytics.vw_payment_recovery_episode AS
WITH sequenced_attempts AS (
    SELECT
        payment.*,
        SUM(
            CASE
                WHEN payment.payment_type IN ('initial', 'renewal') THEN 1
                ELSE 0
            END
        ) OVER (
            PARTITION BY payment.subscription_period_id
            ORDER BY
                payment.payment_timestamp,
                payment.attempt_number,
                payment.payment_id
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS billing_episode_number
    FROM analytics.fact_payment AS payment
    WHERE payment.payment_type IN ('initial', 'renewal', 'retry')
), episode_summary AS (
    SELECT
        subscription_period_id,
        billing_episode_number,
        MIN(customer_id) AS customer_id,
        MIN(payment_timestamp) FILTER (
            WHERE payment_type IN ('initial', 'renewal')
        ) AS episode_started_at,
        MIN(payment_type) FILTER (
            WHERE payment_type IN ('initial', 'renewal')
        ) AS starting_payment_type,
        MIN(payment_timestamp) FILTER (WHERE payment_status = 'failed')
            AS first_failed_at,
        COUNT(*) FILTER (WHERE payment_status = 'failed')
            AS failed_attempt_count
    FROM sequenced_attempts
    WHERE billing_episode_number > 0
    GROUP BY subscription_period_id, billing_episode_number
), recovered AS (
    SELECT
        summary.subscription_period_id,
        summary.billing_episode_number,
        MIN(attempt.payment_timestamp) AS recovered_at
    FROM episode_summary AS summary
    JOIN sequenced_attempts AS attempt
      ON attempt.subscription_period_id = summary.subscription_period_id
     AND attempt.billing_episode_number = summary.billing_episode_number
     AND attempt.payment_status = 'succeeded'
     AND attempt.payment_timestamp >= summary.first_failed_at
    WHERE summary.first_failed_at IS NOT NULL
    GROUP BY summary.subscription_period_id, summary.billing_episode_number
)
SELECT
    summary.subscription_period_id || ':'
        || summary.billing_episode_number::text AS payment_episode_id,
    summary.subscription_period_id,
    summary.billing_episode_number,
    summary.customer_id,
    period.plan_id,
    summary.starting_payment_type,
    summary.episode_started_at,
    summary.first_failed_at,
    recovered.recovered_at,
    summary.failed_attempt_count,
    EXTRACT(EPOCH FROM (recovered.recovered_at - summary.first_failed_at))
        / 3600.0 AS recovery_hours,
    (
        recovered.recovered_at IS NOT NULL
        AND recovered.recovered_at
            <= summary.first_failed_at + INTERVAL '7 days'
    ) AS recovered_within_7_days
FROM episode_summary AS summary
JOIN analytics.fact_subscription_period AS period
  ON period.subscription_period_id = summary.subscription_period_id
LEFT JOIN recovered
  ON recovered.subscription_period_id = summary.subscription_period_id
 AND recovered.billing_episode_number = summary.billing_episode_number
WHERE summary.first_failed_at IS NOT NULL;

-- Grain: one row per randomized campaign assignment. Payments and service cost
-- are independently reduced to the assignment's 90-day value window.
CREATE OR REPLACE VIEW analytics.vw_campaign_assignment_outcome AS
WITH assignment_customer AS (
    SELECT
        assignment.*,
        campaign.campaign_name,
        campaign.objective,
        COALESCE(assignment.customer_id, customer.customer_id)
            AS resolved_customer_id,
        customer.signup_timestamp,
        MAKE_DATE(
            LEFT(assignment.measurement_window_id, 4)::integer,
            (RIGHT(assignment.measurement_window_id, 1)::integer - 1) * 3 + 1,
            1
        ) AS measurement_window_start
    FROM analytics.fact_campaign_assignment AS assignment
    JOIN analytics.dim_campaign AS campaign
      ON campaign.campaign_id = assignment.campaign_id
    LEFT JOIN LATERAL (
        SELECT candidate.customer_id, candidate.signup_timestamp
        FROM analytics.dim_customer AS candidate
        WHERE candidate.prospect_key = assignment.prospect_key
        ORDER BY candidate.signup_timestamp, candidate.customer_id
        LIMIT 1
    ) AS customer ON TRUE
), conversion_event AS (
    SELECT
        assignment_customer.*,
        CASE
            WHEN assignment_customer.objective = 'acquisition'
                THEN assignment_customer.signup_timestamp
            WHEN assignment_customer.objective = 'free-to-paid conversion'
                THEN first_paid_after_assignment.paid_start_timestamp
        END AS conversion_timestamp
    FROM assignment_customer
    LEFT JOIN LATERAL (
        SELECT MIN(period.period_start_timestamp) AS paid_start_timestamp
        FROM analytics.fact_subscription_period AS period
        JOIN analytics.dim_plan AS plan
          ON plan.plan_id = period.plan_id
         AND plan.is_paid_plan
        WHERE period.customer_id = assignment_customer.resolved_customer_id
          AND period.period_start_timestamp >= assignment_customer.assigned_at
    ) AS first_paid_after_assignment ON TRUE
)
SELECT
    event.assignment_id,
    event.campaign_id,
    event.campaign_name,
    event.objective,
    event.measurement_window_id,
    event.measurement_window_start,
    (event.measurement_window_start + INTERVAL '3 months')::date
        AS measurement_window_end_exclusive,
    event.prospect_key,
    event.resolved_customer_id AS customer_id,
    event.assignment_arm,
    event.assigned_at,
    event.first_exposed_at,
    event.conversion_timestamp,
    (event.first_exposed_at IS NOT NULL) AS was_exposed,
    (event.assigned_at + INTERVAL '30 days' <= cutoff.data_through_exclusive)
        AS is_conversion_outcome_mature,
    COALESCE(
        event.conversion_timestamp >= event.assigned_at
        AND event.conversion_timestamp < event.assigned_at + INTERVAL '30 days',
        FALSE
    ) AS converted_within_30_days,
    (event.assigned_at + INTERVAL '90 days' <= cutoff.data_through_exclusive)
        AS is_value_outcome_mature,
    CASE
        WHEN event.assigned_at + INTERVAL '90 days'
                <= cutoff.data_through_exclusive
            THEN COALESCE(value_payment.effective_net_revenue_90d, 0)
    END AS effective_net_revenue_90d,
    CASE
        WHEN event.assigned_at + INTERVAL '90 days'
                <= cutoff.data_through_exclusive
            THEN COALESCE(value_cost.prorated_service_cost_90d, 0)
    END AS prorated_service_cost_90d,
    CASE
        WHEN event.assigned_at + INTERVAL '90 days'
                <= cutoff.data_through_exclusive
            THEN COALESCE(value_payment.effective_net_revenue_90d, 0)
                - COALESCE(value_cost.prorated_service_cost_90d, 0)
    END AS realized_contribution_90d,
    CASE
        WHEN event.assigned_at + INTERVAL '90 days'
                <= cutoff.data_through_exclusive
            THEN COALESCE(value_payment.imputed_amount_events_90d, 0)
    END AS imputed_amount_events_90d,
    (
        (
            event.measurement_window_start + INTERVAL '3 months 30 days'
        ) AT TIME ZONE 'UTC'
        <= cutoff.data_through_exclusive
    ) AS is_measurement_window_final_30d,
    (
        (
            event.measurement_window_start + INTERVAL '3 months 90 days'
        ) AT TIME ZONE 'UTC'
        <= cutoff.data_through_exclusive
    ) AS is_measurement_window_final_90d
FROM conversion_event AS event
CROSS JOIN analytics.vw_reporting_cutoff AS cutoff
LEFT JOIN LATERAL (
    SELECT
        SUM(
            CASE
                WHEN payment.amount IS NOT NULL THEN payment.amount
                WHEN payment.payment_type = 'refund' THEN -plan.monthly_price
                ELSE plan.monthly_price
            END
        ) AS effective_net_revenue_90d,
        COUNT(*) FILTER (WHERE payment.amount IS NULL)
            AS imputed_amount_events_90d
    FROM analytics.fact_payment AS payment
    JOIN analytics.fact_subscription_period AS period
      ON period.subscription_period_id = payment.subscription_period_id
    JOIN analytics.dim_plan AS plan
      ON plan.plan_id = period.plan_id
    WHERE payment.customer_id = event.resolved_customer_id
      AND payment.payment_status = 'succeeded'
      AND payment.payment_timestamp >= event.assigned_at
      AND payment.payment_timestamp < event.assigned_at + INTERVAL '90 days'
) AS value_payment ON TRUE
LEFT JOIN LATERAL (
    SELECT
        ROUND(
            SUM(
                plan.estimated_monthly_variable_cost
                * EXTRACT(EPOCH FROM (
                    LEAST(
                        COALESCE(period.period_end_timestamp, value_window.end_at),
                        month_start + INTERVAL '1 month',
                        value_window.end_at
                    )
                    - GREATEST(
                        period.period_start_timestamp,
                        month_start,
                        event.assigned_at
                    )
                ))::numeric
                / NULLIF(
                    EXTRACT(EPOCH FROM (
                        month_start + INTERVAL '1 month' - month_start
                    ))::numeric,
                    0
                )
            ),
            2
        ) AS prorated_service_cost_90d
    FROM analytics.fact_subscription_period AS period
    JOIN analytics.dim_plan AS plan
      ON plan.plan_id = period.plan_id
    CROSS JOIN LATERAL (
        SELECT LEAST(
            event.assigned_at + INTERVAL '90 days',
            cutoff.data_through_exclusive
        ) AS end_at
    ) AS value_window
    CROSS JOIN LATERAL GENERATE_SERIES(
        DATE_TRUNC(
            'month',
            GREATEST(period.period_start_timestamp, event.assigned_at)
        ),
        DATE_TRUNC(
            'month',
            LEAST(
                COALESCE(period.period_end_timestamp, value_window.end_at),
                value_window.end_at
            ) - INTERVAL '1 microsecond'
        ),
        INTERVAL '1 month'
    ) AS generated_month(month_start)
    WHERE period.customer_id = event.resolved_customer_id
      AND period.period_start_timestamp < value_window.end_at
      AND COALESCE(period.period_end_timestamp, value_window.end_at)
            > event.assigned_at
) AS value_cost ON TRUE;

COMMIT;
