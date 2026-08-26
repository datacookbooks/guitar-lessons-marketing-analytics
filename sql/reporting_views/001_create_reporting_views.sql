BEGIN;

-- Grain: month plus opening paid plan and approved customer segments.
CREATE OR REPLACE VIEW reporting.vw_monthly_paid_movement AS
SELECT
    state.month_start,
    state.is_complete_month,
    state.opening_plan_id,
    state.paid_cohort_month,
    state.initial_acquisition_channel,
    state.first_campaign_id,
    state.state,
    state.experience_level,
    state.paid_tenure_band,
    COUNT(*) FILTER (WHERE state.is_opening_paid_customer)
        AS opening_paid_customers,
    COUNT(*) FILTER (WHERE state.churned_opening_customer)
        AS churned_paid_customers,
    SUM(state.upgrade_events) AS upgrade_events,
    SUM(state.downgrade_events) AS downgrade_events,
    SUM(state.reactivation_events) AS reactivation_events,
    COUNT(*) FILTER (WHERE state.is_closing_paid_customer)
        AS closing_paid_customers,
    COUNT(*) FILTER (WHERE state.churned_opening_customer)::numeric
        / NULLIF(
            COUNT(*) FILTER (WHERE state.is_opening_paid_customer),
            0
        ) AS paid_churn_rate
FROM analytics.vw_customer_monthly_subscription_state AS state
GROUP BY
    state.month_start,
    state.is_complete_month,
    state.opening_plan_id,
    state.paid_cohort_month,
    state.initial_acquisition_channel,
    state.first_campaign_id,
    state.state,
    state.experience_level,
    state.paid_tenure_band;

-- Grain: paid cohort, exact anniversary checkpoint, initial paid plan, and
-- approved acquisition/customer segments. Only mature checkpoints contribute.
CREATE OR REPLACE VIEW reporting.vw_paid_cohort_retention AS
SELECT
    clv.paid_cohort_month,
    clv.month_number,
    clv.initial_paid_plan_id,
    clv.initial_acquisition_channel,
    clv.first_campaign_id,
    clv.state,
    clv.experience_level,
    COUNT(*) AS eligible_customers,
    COUNT(*) FILTER (WHERE clv.is_retained_paid) AS retained_customers,
    COUNT(*) FILTER (WHERE clv.is_retained_paid)::numeric
        / NULLIF(COUNT(*), 0) AS paid_retention_rate
FROM analytics.vw_customer_clv_month AS clv
WHERE clv.is_checkpoint_mature
GROUP BY
    clv.paid_cohort_month,
    clv.month_number,
    clv.initial_paid_plan_id,
    clv.initial_acquisition_channel,
    clv.first_campaign_id,
    clv.state,
    clv.experience_level;

-- Grain: failure month, episode plan/type, and approved customer segments.
CREATE OR REPLACE VIEW reporting.vw_payment_recovery AS
SELECT
    DATE_TRUNC('month', episode.first_failed_at)::date AS failure_month,
    episode.plan_id,
    episode.starting_payment_type,
    customer.initial_acquisition_channel,
    customer.first_campaign_id,
    customer.state,
    customer.experience_level,
    COUNT(*) AS failed_billing_episodes,
    COUNT(*) FILTER (WHERE episode.recovered_within_7_days)
        AS recovered_episodes,
    SUM(episode.failed_attempt_count) AS failed_payment_attempts,
    AVG(episode.recovery_hours) FILTER (
        WHERE episode.recovered_within_7_days
    ) AS average_recovery_hours,
    COUNT(*) FILTER (WHERE episode.recovered_within_7_days)::numeric
        / NULLIF(COUNT(*), 0) AS payment_recovery_rate
FROM analytics.vw_payment_recovery_episode AS episode
JOIN analytics.dim_customer AS customer
  ON customer.customer_id = episode.customer_id
GROUP BY
    DATE_TRUNC('month', episode.first_failed_at)::date,
    episode.plan_id,
    episode.starting_payment_type,
    customer.initial_acquisition_channel,
    customer.first_campaign_id,
    customer.state,
    customer.experience_level;

-- Grain: customer and calendar month. Monetary columns are additive.
CREATE OR REPLACE VIEW reporting.vw_customer_value AS
SELECT
    value.customer_id,
    value.month_start,
    customer.signup_timestamp,
    customer.initial_acquisition_channel,
    customer.first_campaign_id,
    customer.state,
    customer.experience_level,
    value.reported_net_revenue,
    value.effective_charge_revenue,
    value.effective_refund_amount,
    value.effective_net_revenue,
    value.prorated_service_cost,
    value.realized_contribution,
    value.imputed_amount_events,
    value.succeeded_payment_events
FROM analytics.vw_customer_monthly_contribution AS value
JOIN analytics.dim_customer AS customer
  ON customer.customer_id = value.customer_id;

-- Grain: segment level, initial paid plan, optional acquisition channel, and
-- paid-tenure month 1-12. The plan-only rows provide the documented fallback.
CREATE OR REPLACE VIEW reporting.vw_paid_clv_monthly_component AS
SELECT
    CASE
        WHEN GROUPING(clv.initial_acquisition_channel) = 1 THEN 'plan'
        ELSE 'plan_and_channel'
    END AS segment_level,
    clv.initial_paid_plan_id,
    CASE
        WHEN GROUPING(clv.initial_acquisition_channel) = 1 THEN NULL
        ELSE clv.initial_acquisition_channel
    END AS initial_acquisition_channel,
    clv.month_number,
    COUNT(*) AS eligible_customers,
    COUNT(*) FILTER (WHERE clv.is_retained_paid) AS retained_customers,
    COUNT(*) FILTER (WHERE clv.is_retained_paid)::numeric
        / NULLIF(COUNT(*), 0) AS survival_probability,
    AVG(clv.conditional_standard_contribution_margin) FILTER (
        WHERE clv.is_retained_paid
    ) AS conditional_standard_contribution_margin,
    SUM(
        COALESCE(clv.conditional_standard_contribution_margin, 0)
    ) / NULLIF(COUNT(*), 0) AS expected_monthly_contribution
FROM analytics.vw_customer_clv_month AS clv
WHERE clv.is_checkpoint_mature
GROUP BY GROUPING SETS (
    (
        clv.initial_paid_plan_id,
        clv.initial_acquisition_channel,
        clv.month_number
    ),
    (clv.initial_paid_plan_id, clv.month_number)
);

-- Grain: initial paid plan and optional acquisition channel.
CREATE OR REPLACE VIEW reporting.vw_expected_12m_paid_clv AS
WITH clv_component AS (
    SELECT *
    FROM reporting.vw_paid_clv_monthly_component
), new_paid_weight AS (
    SELECT
        CASE
            WHEN GROUPING(cohort.initial_acquisition_channel) = 1 THEN 'plan'
            ELSE 'plan_and_channel'
        END AS segment_level,
        cohort.initial_paid_plan_id,
        CASE
            WHEN GROUPING(cohort.initial_acquisition_channel) = 1 THEN NULL
            ELSE cohort.initial_acquisition_channel
        END AS initial_acquisition_channel,
        COUNT(*) AS new_paid_weight
    FROM analytics.vw_customer_paid_cohort AS cohort
    GROUP BY GROUPING SETS (
        (
            cohort.initial_paid_plan_id,
            cohort.initial_acquisition_channel
        ),
        (cohort.initial_paid_plan_id)
    )
)
SELECT
    component.segment_level,
    component.initial_paid_plan_id,
    component.initial_acquisition_channel,
    weight.new_paid_weight,
    COUNT(*) AS observed_tenure_months,
    MIN(component.eligible_customers) AS minimum_monthly_eligible_customers,
    CASE
        WHEN COUNT(*) = 12
         AND MIN(component.eligible_customers) >= 30
            THEN SUM(component.expected_monthly_contribution)
    END AS expected_12m_paid_clv,
    (COUNT(*) = 12 AND MIN(component.eligible_customers) >= 30)
        AS is_sample_sufficient
FROM clv_component AS component
JOIN new_paid_weight AS weight
  ON weight.segment_level = component.segment_level
 AND weight.initial_paid_plan_id = component.initial_paid_plan_id
 AND weight.initial_acquisition_channel
        IS NOT DISTINCT FROM component.initial_acquisition_channel
GROUP BY
    component.segment_level,
    component.initial_paid_plan_id,
    component.initial_acquisition_channel,
    weight.new_paid_weight;

-- Grain: campaign and metric date. Components remain additive. Exact-day rates
-- are exposed for SQL validation and must be recomputed after dashboard rollup.
CREATE OR REPLACE VIEW reporting.vw_campaign_daily_performance AS
SELECT
    daily.metric_date,
    daily.campaign_id,
    campaign.campaign_name,
    campaign.channel,
    campaign.objective,
    daily.impressions,
    daily.clicks,
    daily.spend,
    daily.platform_attributed_conversions,
    (daily.clicks IS NULL) AS is_clicks_missing,
    (daily.spend IS NULL) AS is_spend_missing,
    (daily.metric_date <= cutoff.data_through_date) AS is_complete_date,
    CASE
        WHEN daily.clicks IS NOT NULL THEN
            daily.clicks::numeric / NULLIF(daily.impressions, 0)
    END AS click_through_rate,
    CASE
        WHEN daily.clicks IS NOT NULL AND daily.spend IS NOT NULL THEN
            daily.spend / NULLIF(daily.clicks, 0)
    END AS cost_per_click,
    CASE
        WHEN daily.spend IS NOT NULL THEN
            daily.spend / NULLIF(daily.platform_attributed_conversions, 0)
    END AS platform_cost_per_attributed_conversion
FROM analytics.fact_campaign_daily AS daily
JOIN analytics.dim_campaign AS campaign
  ON campaign.campaign_id = daily.campaign_id
CROSS JOIN analytics.vw_reporting_cutoff AS cutoff;

-- Grain: campaign, measurement window, and original randomized arm.
CREATE OR REPLACE VIEW reporting.vw_campaign_experiment_arm AS
SELECT
    outcome.campaign_id,
    outcome.campaign_name,
    outcome.objective,
    outcome.measurement_window_id,
    outcome.measurement_window_start,
    outcome.measurement_window_end_exclusive,
    outcome.assignment_arm,
    COUNT(*) AS assigned_eligible_people,
    COUNT(*) FILTER (WHERE outcome.was_exposed) AS exposed_assignments,
    COUNT(*) FILTER (WHERE outcome.is_conversion_outcome_mature)
        AS mature_conversion_assignments,
    COUNT(*) FILTER (
        WHERE outcome.is_conversion_outcome_mature
          AND outcome.converted_within_30_days
    ) AS conversions_30d,
    COUNT(*) FILTER (WHERE outcome.is_value_outcome_mature)
        AS mature_value_assignments,
    SUM(outcome.effective_net_revenue_90d) FILTER (
        WHERE outcome.is_value_outcome_mature
    ) AS effective_net_revenue_90d,
    SUM(outcome.prorated_service_cost_90d) FILTER (
        WHERE outcome.is_value_outcome_mature
    ) AS prorated_service_cost_90d,
    SUM(outcome.realized_contribution_90d) FILTER (
        WHERE outcome.is_value_outcome_mature
    ) AS realized_contribution_90d,
    SUM(outcome.imputed_amount_events_90d) FILTER (
        WHERE outcome.is_value_outcome_mature
    ) AS imputed_amount_events_90d,
    BOOL_AND(outcome.is_measurement_window_final_30d)
        AS is_measurement_window_final_30d,
    BOOL_AND(outcome.is_measurement_window_final_90d)
        AS is_measurement_window_final_90d,
    COUNT(*) FILTER (
        WHERE outcome.is_conversion_outcome_mature
          AND outcome.converted_within_30_days
    )::numeric
        / NULLIF(
            COUNT(*) FILTER (WHERE outcome.is_conversion_outcome_mature),
            0
        ) AS conversion_rate_30d,
    SUM(outcome.effective_net_revenue_90d) FILTER (
        WHERE outcome.is_value_outcome_mature
    ) / NULLIF(
        COUNT(*) FILTER (WHERE outcome.is_value_outcome_mature),
        0
    ) AS mean_effective_net_revenue_90d,
    SUM(outcome.realized_contribution_90d) FILTER (
        WHERE outcome.is_value_outcome_mature
    ) / NULLIF(
        COUNT(*) FILTER (WHERE outcome.is_value_outcome_mature),
        0
    ) AS mean_realized_contribution_90d
FROM analytics.vw_campaign_assignment_outcome AS outcome
WHERE outcome.campaign_id <> -1
GROUP BY
    outcome.campaign_id,
    outcome.campaign_name,
    outcome.objective,
    outcome.measurement_window_id,
    outcome.measurement_window_start,
    outcome.measurement_window_end_exclusive,
    outcome.assignment_arm;

-- Grain: campaign and measurement window. Assignment/value facts and campaign
-- daily spend are each reduced to window grain before their only join.
CREATE OR REPLACE VIEW reporting.vw_campaign_incremental_performance AS
WITH arm AS (
    SELECT *
    FROM reporting.vw_campaign_experiment_arm
), window_bounds AS (
    SELECT DISTINCT
        campaign_id,
        campaign_name,
        objective,
        measurement_window_id,
        measurement_window_start,
        measurement_window_end_exclusive
    FROM arm
), window_spend AS (
    SELECT
        bounds.campaign_id,
        bounds.measurement_window_id,
        SUM(daily.spend) AS spend,
        COUNT(*) FILTER (
            WHERE daily.metric_date IS NOT NULL
              AND daily.spend IS NULL
        ) AS missing_spend_days
    FROM window_bounds AS bounds
    LEFT JOIN analytics.fact_campaign_daily AS daily
      ON daily.campaign_id = bounds.campaign_id
     AND daily.metric_date >= bounds.measurement_window_start
     AND daily.metric_date < bounds.measurement_window_end_exclusive
    GROUP BY bounds.campaign_id, bounds.measurement_window_id
), components AS (
    SELECT
        arm.campaign_id,
        MAX(arm.campaign_name) AS campaign_name,
        MAX(arm.objective) AS objective,
        arm.measurement_window_id,
        MIN(arm.measurement_window_start) AS measurement_window_start,
        MAX(arm.measurement_window_end_exclusive)
            AS measurement_window_end_exclusive,
        BOOL_AND(arm.is_measurement_window_final_30d)
            AS is_measurement_window_final_30d,
        BOOL_AND(arm.is_measurement_window_final_90d)
            AS is_measurement_window_final_90d,
        SUM(arm.mature_conversion_assignments) FILTER (
            WHERE arm.assignment_arm = 'treatment'
        ) AS treatment_eligible_30d,
        SUM(arm.conversions_30d) FILTER (
            WHERE arm.assignment_arm = 'treatment'
        ) AS treatment_conversions_30d,
        SUM(arm.mature_conversion_assignments) FILTER (
            WHERE arm.assignment_arm = 'holdout'
        ) AS holdout_eligible_30d,
        SUM(arm.conversions_30d) FILTER (
            WHERE arm.assignment_arm = 'holdout'
        ) AS holdout_conversions_30d,
        SUM(arm.mature_value_assignments) FILTER (
            WHERE arm.assignment_arm = 'treatment'
        ) AS treatment_eligible_90d,
        SUM(arm.effective_net_revenue_90d) FILTER (
            WHERE arm.assignment_arm = 'treatment'
        ) AS treatment_revenue_90d,
        SUM(arm.realized_contribution_90d) FILTER (
            WHERE arm.assignment_arm = 'treatment'
        ) AS treatment_contribution_90d,
        SUM(arm.mature_value_assignments) FILTER (
            WHERE arm.assignment_arm = 'holdout'
        ) AS holdout_eligible_90d,
        SUM(arm.effective_net_revenue_90d) FILTER (
            WHERE arm.assignment_arm = 'holdout'
        ) AS holdout_revenue_90d,
        SUM(arm.realized_contribution_90d) FILTER (
            WHERE arm.assignment_arm = 'holdout'
        ) AS holdout_contribution_90d,
        SUM(arm.imputed_amount_events_90d) AS imputed_amount_events_90d
    FROM arm
    GROUP BY arm.campaign_id, arm.measurement_window_id
), lift AS (
    SELECT
        components.*,
        CASE
            WHEN components.is_measurement_window_final_30d THEN
                components.treatment_conversions_30d::numeric
                    / NULLIF(components.treatment_eligible_30d, 0)
                - components.holdout_conversions_30d::numeric
                    / NULLIF(components.holdout_eligible_30d, 0)
        END AS incremental_conversion_lift,
        CASE
            WHEN components.is_measurement_window_final_90d THEN
                components.treatment_eligible_90d
                * (
                    components.treatment_revenue_90d
                        / NULLIF(components.treatment_eligible_90d, 0)
                    - components.holdout_revenue_90d
                        / NULLIF(components.holdout_eligible_90d, 0)
                )
        END AS estimated_incremental_revenue_90d,
        CASE
            WHEN components.is_measurement_window_final_90d THEN
                components.treatment_eligible_90d
                * (
                    components.treatment_contribution_90d
                        / NULLIF(components.treatment_eligible_90d, 0)
                    - components.holdout_contribution_90d
                        / NULLIF(components.holdout_eligible_90d, 0)
                )
        END AS estimated_incremental_contribution_90d
    FROM components
), incremental AS (
    SELECT
        lift.*,
        CASE
            WHEN lift.is_measurement_window_final_30d THEN
                lift.treatment_eligible_30d * lift.incremental_conversion_lift
        END AS estimated_incremental_customers_30d
    FROM lift
)
SELECT
    incremental.campaign_id,
    incremental.campaign_name,
    incremental.objective,
    incremental.measurement_window_id,
    incremental.measurement_window_start,
    incremental.measurement_window_end_exclusive,
    incremental.is_measurement_window_final_30d,
    incremental.is_measurement_window_final_90d,
    spend.spend,
    spend.missing_spend_days,
    incremental.treatment_eligible_30d,
    incremental.treatment_conversions_30d,
    incremental.holdout_eligible_30d,
    incremental.holdout_conversions_30d,
    incremental.incremental_conversion_lift,
    incremental.estimated_incremental_customers_30d,
    CASE
        WHEN incremental.estimated_incremental_customers_30d > 0
         AND spend.spend > 0
         AND spend.missing_spend_days = 0
            THEN spend.spend
                / incremental.estimated_incremental_customers_30d
    END AS incremental_customer_acquisition_cost,
    incremental.treatment_eligible_90d,
    incremental.treatment_revenue_90d,
    incremental.treatment_contribution_90d,
    incremental.holdout_eligible_90d,
    incremental.holdout_revenue_90d,
    incremental.holdout_contribution_90d,
    incremental.estimated_incremental_revenue_90d,
    incremental.estimated_incremental_contribution_90d,
    incremental.imputed_amount_events_90d,
    CASE
        WHEN incremental.is_measurement_window_final_90d
         AND spend.spend > 0
         AND spend.missing_spend_days = 0
            THEN incremental.estimated_incremental_revenue_90d
                / spend.spend
    END AS incremental_roas_90d,
    CASE
        WHEN incremental.is_measurement_window_final_90d
         AND spend.spend > 0
         AND spend.missing_spend_days = 0
            THEN (
                incremental.estimated_incremental_contribution_90d
                - spend.spend
            ) / spend.spend
    END AS incremental_roi_90d
FROM incremental
JOIN window_spend AS spend
  ON spend.campaign_id = incremental.campaign_id
 AND spend.measurement_window_id = incremental.measurement_window_id;

-- Grain: source table, issue code, and selected-delivery status.
CREATE OR REPLACE VIEW reporting.vw_data_quality AS
SELECT
    issue.source_table,
    issue.issue_code,
    issue.is_selected_delivery,
    COUNT(*) AS issue_records,
    COUNT(DISTINCT issue.raw_row_id) AS impacted_raw_rows,
    MIN(issue.detected_at) AS first_detected_at,
    MAX(issue.detected_at) AS last_detected_at
FROM analytics.data_quality_issue AS issue
GROUP BY
    issue.source_table,
    issue.issue_code,
    issue.is_selected_delivery;

COMMIT;
