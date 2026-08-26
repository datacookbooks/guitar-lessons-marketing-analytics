# Reporting metric definitions

## Purpose and status

This document is the contract for the analytics helper views, dashboard-facing
reporting views, and eventual BI semantic model. It is written before the views
so implementation and validation can be reviewed against stable business rules.

The live source profile was executed through a PostgreSQL read-only transaction
against the reviewed analytics snapshot. All core business sources cover
2024-01-01 through 2026-08-25. August 2026 and measurement window `2026_Q3` are
in progress and must not be presented as complete periods.

## Profile findings that constrain the design

- Subscription histories contain no overlaps. All upgrades, downgrades, and
  paid-to-Free movements begin the next plan at the exact prior-period end.
- There are 109 cancellation/reactivation sequences with a positive gap: 104
  from Pro and 15 from Master. Cancellation remains a churn event even if a
  customer later reactivates.
- The exploratory monthly churn series ranges from 5.10% to 10.87% in complete
  months after the initial empty opening snapshot. The final partial month is
  not a finalized rate.
- The source contains 5,224 failed payment events. Event-level matching found
  2,969 later successful retries, all within three days. The production metric
  must group attempts into billing episodes so one recovery is not matched to
  several failures.
- Successful payment amounts contain 167 null values and refunds contain two
  null values. Every non-null charge equals the applicable standard plan price,
  and every non-null refund is the negative standard price. Base analytics
  values remain null; any reporting-layer substitution must be explicit and
  flagged.
- Campaign delivery contains 25 null-click days and ten null-spend days.
  Aggregated rates must expose these completeness counts rather than silently
  treating missing measures as zero.
- Campaign assignments contain no duplicate person/study rows, no cross-arm
  conflicts, no simultaneous acquisition studies, and no null prospect keys.
- The 285 controlled unknown-campaign raw-delivery quality records remain in
  data-quality reporting. After deterministic delivery selection, 278 analytics
  assignment rows map to campaign `-1`; those selected rows reconcile to the
  helper layer but are excluded from named-campaign experiment results.
- Derived assignment conversions are identical at 30, 60, and 90 days in the
  current simulation. The conversion horizon is therefore fixed at 30 days.
  Recent assignments are not nonconverters until that entire window is
  observable.

## Common time and eligibility contract

### Data cutoff

`data_through_exclusive` is midnight immediately after the latest date for
which all sources needed by a metric are available. Cross-subject views use the
minimum of their required source maxima. A row or outcome is mature only when
its entire required observation window ends on or before this timestamp.

### Interval convention

All timestamp intervals are half-open: `[start_timestamp, end_timestamp)`.
This makes a same-instant plan transition belong to exactly one plan and avoids
double counting.

### Period completeness

- A calendar month is complete when its next month start is on or before
  `data_through_exclusive`.
- A 30-day conversion outcome is mature when `assigned_at + interval '30 days'`
  is on or before `data_through_exclusive`.
- A 90-day campaign value outcome is mature when
  `assigned_at + interval '90 days'` is on or before the cutoff.
- A quarterly experiment is final only after the quarter is closed and every
  assignment in it has the required mature outcome window.
- A CLV checkpoint is eligible only when the individual customer's paid-start
  anniversary is observable. Recent customers are not counted as lost.

### Null and denominator rules

- Never convert a missing denominator to zero. Rates use `NULLIF(denominator, 0)`.
- Zero is a valid observed amount or count; null means unknown or not applicable.
- Additive monetary totals expose missing/imputed-event counts beside the value.
- A dashboard may show an explicitly labeled estimate that uses a documented
  substitution, but it must not overwrite the typed source value.
- Unknown campaign `-1` is retained for quality counts and excluded from named
  campaign performance.

## Intended helper-view contract

| Helper view | Declared grain | Primary responsibility |
|---|---|---|
| `analytics.vw_customer_paid_cohort` | One row per customer who has ever started paid service | First-paid timestamp/month, initial paid plan, acquisition attributes |
| `analytics.vw_customer_monthly_subscription_state` | One row per customer and calendar month in which the customer is relevant to paid-state analysis | Opening/closing paid state, opening/closing plan, churn and plan-movement flags, complete-month flag |
| `analytics.vw_customer_monthly_contribution` | One row per customer and calendar month | Charges, refunds, effective revenue, prorated service cost, realized contribution, completeness flags |
| `analytics.vw_customer_clv_month` | One row per paid-cohort customer and paid-tenure checkpoint 1 through 12 | Anniversary maturity, retained-paid flag, active plan, conditional standard contribution margin |
| `analytics.vw_payment_recovery_episode` | One row per billing episode that contains at least one failed attempt | First failure, later success, time to recovery, seven-day recovery flag |
| `analytics.vw_campaign_assignment_outcome` | One row per assignment | ITT arm, conversion event, 30-day maturity/conversion, 90-day revenue and contribution, maturity flags |

Helper views preserve customer- or assignment-level detail for reusable slicing.
They do not join two uncontrolled fact tables at detail grain. Payment and
service-cost components are first reduced to customer-month; campaign outcomes
are first reduced to one assignment.

## Intended reporting-view contract

| Reporting view | Declared grain | Additive components exposed |
|---|---|---|
| `reporting.vw_monthly_paid_movement` | Month plus opening plan and approved customer segments | Opening paid exposures, churned opening customers, upgrade events, downgrade events, reactivations, closing paid snapshot |
| `reporting.vw_paid_cohort_retention` | Cohort month, anniversary month number, initial paid plan, and approved acquisition segments | Eligible customers and retained paid customers |
| `reporting.vw_payment_recovery` | Failure month, plan, attempt type, and approved customer segments | Failed billing episodes and recovered episodes |
| `reporting.vw_customer_value` | Customer and calendar month | Charges, refunds, service cost, realized contribution, imputed/missing event counts |
| `reporting.vw_expected_12m_paid_clv` | Initial paid plan and acquisition segment | Eligible-customer counts, monthly survival/margin components, expected 12-month paid CLV |
| `reporting.vw_campaign_daily_performance` | Metric date and campaign | Impressions, clicks, spend, platform-attributed conversions, missing-measure flags |
| `reporting.vw_campaign_experiment_arm` | Campaign, measurement window, and assignment arm | Mature eligible assignments, 30-day conversions, 90-day revenue, 90-day contribution |
| `reporting.vw_campaign_incremental_performance` | Campaign and final measurement window | Treatment/holdout components, spend, estimated incremental customers/revenue/contribution |
| `reporting.vw_data_quality` | Source table, issue code, selected-delivery flag | Issue records and distinct impacted raw rows |

## Metric specifications

### 1. Monthly paid-logo churn

| Field | Contract |
|---|---|
| Business question | What share of customers who were paying at the start of a month left paid service during that month? |
| Output grain | Month plus the customer's opening paid plan, first-paid cohort, initial acquisition channel, first campaign, state, experience level, and documented tenure band |
| Eligible population / denominator | Distinct customers active on Pro or Master at `month_start` |
| Numerator | Eligible opening customers whose opening paid-service spell ends during the month through cancellation or paid-to-Free movement |
| Date basis | Opening snapshot at `month_start`; event timestamp in `[month_start, next_month_start)`; only complete months are final |
| Null and edge treatment | Opening customers are deduplicated before aggregation. A cancellation followed by reactivation is still churn. A customer who first becomes paid after month start is not in that month's numerator or denominator. Pro/Master movement is excluded. Zero denominator returns null. |
| Aggregation class | Churned customers and opening customer-month exposures are additive across mutually exclusive slices. The rate is non-additive and must be recomputed. Opening/closing snapshots are semi-additive across time. |
| Permitted rollups | Sum numerator and denominator across compatible months and mutually exclusive segment rows, then divide. Never average monthly rate columns. For a point-in-time customer count, use the latest selected snapshot rather than summing months. |
| SQL responsibility | Establish opening eligibility, classify events, restrict numerator to the opening population, create segment keys, and expose numerator/denominator plus exact-grain rate |
| Intended DAX | `[Paid Churn Rate] = DIVIDE(SUM([churned_paid_customers]), SUM([opening_paid_customers]))`; percentage, two decimals. `[Opening Paid Customers Snapshot]` uses the latest visible `month_start`. |
| Semantic-model source / relationships | `reporting.vw_monthly_paid_movement`; many-to-one to Date by `month_start`, Plan by `opening_plan_id`, Campaign by `first_campaign_id`, and approved customer-segment dimensions |

### 2. Upgrades, downgrades, reactivations, and paid snapshots

| Field | Contract |
|---|---|
| Business question | How do customers move within paid service, and how many return after cancellation? |
| Output grain | Event month and from/to plan for movements; customer/month in the helper; segment grain in reporting |
| Numerator / denominator | Upgrade and downgrade metrics count transition events. Reactivation counts a paid start following a cancellation gap. An optional reactivation rate divides reactivations by previously cancelled customers eligible to return at month start. |
| Date basis | Transition timestamp; reactivation uses the later paid-period start. Opening and closing snapshots use month boundaries. |
| Null and edge treatment | Same-instant Free-to-paid is a conversion, not reactivation. Same-instant Pro/Master movement is upgrade/downgrade. Multiple legitimate movements are separate events; distinct-customer measures remain non-additive. |
| Aggregation class / rollups | Event counts are additive. Distinct customers and snapshots are non-additive or semi-additive. Rates recompute from components. |
| SQL responsibility | Sequence plan periods with `LAG`/`LEAD`, classify exact transitions and positive gaps, and expose from/to keys and components |
| Intended DAX | Sum event components; use `DISTINCTCOUNT` only on helper keys when a distinct-customer visual is required. Snapshot measure uses the latest visible date. |
| Semantic-model source / relationships | Monthly movement view to Date and two role-playing Plan relationships (`from_plan_id`, `to_plan_id`); only the active relationship required by a visual is used |

### 3. Paid-cohort retention

| Field | Contract |
|---|---|
| Business question | What fraction of a first-paid cohort remains paid after 1, 3, 6, and 12 months? |
| Output grain | First-paid cohort month, customer-specific anniversary number, initial paid plan, and acquisition segments |
| Eligible population / denominator | Customers whose exact `first_paid_at + n months` anniversary is fully observable |
| Numerator | Eligible customers active on any paid plan at the exact anniversary timestamp |
| Date basis | Customer-specific paid anniversary, not merely the first day of a later calendar month |
| Null and edge treatment | A reactivated customer active at the checkpoint is retained at that checkpoint. A customer between paid spells is not. Recent customers are excluded until mature. Plan movement does not break paid retention. Zero denominator returns null. |
| Aggregation class | Eligible and retained counts are additive across mutually exclusive segments for one checkpoint. Retention rate is non-additive. The same customers recur across checkpoints, so counts must not be summed across `month_number`. |
| Permitted rollups | Recompute from summed counts while exactly one checkpoint is selected. Return blank if several checkpoint numbers are selected together. |
| SQL responsibility | Define first-paid cohort, exact anniversaries, maturity, active paid state, segment keys, and count components |
| Intended DAX | `[Paid Cohort Retention Rate] = IF(HASONEVALUE([month_number]), DIVIDE(SUM([retained_customers]), SUM([eligible_customers])))`; percentage, two decimals |
| Semantic-model source / relationships | `reporting.vw_paid_cohort_retention`; Date relationship to `cohort_month`; Plan to `initial_paid_plan_id`; disconnected or dedicated tenure-checkpoint dimension for `month_number` |

### 4. Payment-failure recovery

| Field | Contract |
|---|---|
| Business question | What share of billing episodes with a failed attempt recover through a successful charge within seven days? |
| Output grain | Helper: one failed billing episode. Reporting: first-failure month, plan, starting payment type, and customer segments. |
| Eligible population / denominator | Billing episodes containing at least one failed initial, renewal, or retry attempt |
| Numerator | Eligible episodes with a later successful charge in the same episode no more than seven days after the first failure |
| Episode rule | Within each subscription period, an `initial` or `renewal` event starts a billing episode; following retries remain in that episode until the next initial/renewal event. Business timestamps and payment IDs break ties. |
| Null and edge treatment | Failed events are not revenue. Several failed attempts in one episode contribute one denominator. One successful retry recovers one episode. Missing timestamps make an episode ineligible and measurable as a quality exception. |
| Aggregation class / rollups | Failed and recovered episode counts are additive because each episode appears once. Recovery rate is non-additive and recomputed from totals. |
| SQL responsibility | Sequence attempts, assign episode numbers, select first failure and first later success, calculate recovery hours, and expose the seven-day flag |
| Intended DAX | `[Payment Recovery Rate] = DIVIDE(SUM([recovered_episodes]), SUM([failed_billing_episodes]))`; percentage, two decimals |
| Semantic-model source / relationships | `reporting.vw_payment_recovery`; Date to `first_failure_date`, Plan by episode plan, Customer dimension for approved slices |

### 5. Realized customer contribution value

| Field | Contract |
|---|---|
| Business question | How much contribution value has each customer actually generated after refunds and estimated service cost? |
| Output grain | Customer and calendar month, with additive components retained |
| Formula | `effective successful charges + effective refunds - prorated variable service cost` (refund amounts are negative) |
| Revenue eligibility | Only succeeded payment events. Failed attempts contribute zero revenue. Initial, renewal, and retry successes are charges; successful refunds reduce revenue. |
| Cost rule | Allocate each plan's estimated monthly variable cost by the fraction of each calendar month that the subscription period is active, capped at `data_through_exclusive`. This includes Free service and service during failed-payment periods. |
| Null and substitution rule | Base `amount` remains null. Because the project has fixed list prices and no discounts/coupons, a null succeeded charge is explicitly estimated at plan monthly price and a null refund at negative plan price. Expose `is_amount_imputed`, imputed-event count, reported amount, and effective amount. No other value is imputed. |
| Edge treatment | Same-instant plan transitions use half-open intervals, so cost is allocated once. Refunds do not reverse already incurred service cost. Partial calendar months are prorated; incomplete current-month cost stops at the cutoff. |
| Aggregation class / rollups | Charges, refunds, service cost, and contribution are additive after reduction to customer-month. Averages and per-customer summaries are non-additive and recomputed. |
| SQL responsibility | Reduce payments to customer-month, allocate plan cost without overlap, expose reported/effective values and completeness flags, then join the compatible customer-month components |
| Intended DAX | `[Realized Contribution] = SUM([realized_contribution])`, currency. `[Customers With Imputed Value]` is a distinct count from helper keys and must not be summed across months. |
| Semantic-model source / relationships | `reporting.vw_customer_value`; many-to-one to Customer and Date by `month_start`; do not directly join campaign daily facts |

### 6. Expected 12-month paid contribution CLV

| Field | Contract |
|---|---|
| Business question | What 12-month contribution should be expected after a customer first enters paid service? |
| Output grain | Initial paid plan and acquisition segment, with one component row per tenure month 1 through 12 available underneath |
| Eligible population | Customers at first paid start whose applicable anniversary is observable; Free-only customers are outside this paid-CLV metric |
| Survival numerator / denominator | Customers active on any paid plan at anniversary `m` divided by customers eligible for checkpoint `m` |
| Conditional margin | Average standard monthly contribution margin (`monthly_price - estimated_monthly_variable_cost`) of retained customers' active paid plans at checkpoint `m` |
| Formula | Sum for months 1-12 of `survival_probability_m * conditional_margin_m` |
| Null and edge treatment | Paid reactivation counts only if active at the anniversary. Plan changes affect the active margin. Segment output is null when any checkpoint has fewer than 30 eligible customers; plan-only fallback remains reportable with its own eligibility counts. The estimate is not realized value. |
| Aggregation class / rollups | Survival, margin, and CLV are non-additive. Segment CLVs must not be summed or unweighted-averaged. Portfolio CLV uses a weighted average based on the selected new-paid-customer mix. |
| SQL responsibility | Build customer anniversary inputs, maturity and paid-state flags, segment sample sizes, monthly survival/margin components, and reviewed 12-month estimate |
| Intended DAX | `[Expected 12M Paid CLV]` uses the SQL segment estimate at one compatible grain; broader selections use `DIVIDE(SUMX(segment, [expected_clv] * [new_paid_weight]), SUM([new_paid_weight]))`; currency |
| Semantic-model source / relationships | `reporting.vw_expected_12m_paid_clv`; Plan to `initial_paid_plan_id`; acquisition channel/campaign dimensions; tenure component table related through a segment key if exposed |

### 7. Daily campaign delivery and platform attribution

| Field | Contract |
|---|---|
| Business question | How much delivery, engagement, spend, and platform-reported conversion did each campaign record? |
| Output grain | Metric date and campaign |
| Components | Impressions, clicks, spend, and `platform_attributed_conversions`; also null-click and null-spend flags |
| Rates | CTR = clicks / impressions; CPC = spend / clicks; platform CPA = spend / platform-attributed conversions |
| Date basis | `metric_date`; incomplete current dates remain visible but are not silently treated as complete |
| Null and edge treatment | Zero spend is valid for Email. A rate is null when its required component is missing or its denominator is zero. Multi-day completeness counts are summed and shown. |
| Attribution boundary | The platform count is descriptive attribution, not causal lift. The current sources do not contain an auditable customer-level touch key for every campaign, so the project does not invent attributed customer revenue or attributed ROAS from a detail-level fact join. Causal revenue and contribution are reported from assignment experiments. |
| Aggregation class / rollups | Delivery components are additive. CTR, CPC, and CPA are non-additive and recomputed from summed components. |
| SQL responsibility | Expose corrected daily components, completeness flags, and exact-day rates |
| Intended DAX | `[CTR] = DIVIDE(SUM([clicks]), SUM([impressions]))`; `[CPC] = DIVIDE(SUM([spend]), SUM([clicks]))`; `[Platform CPA] = DIVIDE(SUM([spend]), SUM([platform_attributed_conversions]))` |
| Semantic-model source / relationships | `reporting.vw_campaign_daily_performance`; many-to-one to Date by `metric_date` and Campaign by `campaign_id` |

### 8. ITT conversion lift, estimated incremental customers, and CAC

| Field | Contract |
|---|---|
| Business question | How many additional 30-day conversions did assignment to a campaign cause? |
| Output grain | Arm helper at campaign/window/arm; finalized comparison at campaign/measurement window |
| Eligible population | Original randomized assignments for known campaigns whose 30-day outcome is mature; analysis remains intention-to-treat regardless of exposure |
| Conversion event | Acquisition campaigns: registration after assignment. Nurture: first paid-plan start after assignment. Both require event time in `[assigned_at, assigned_at + 30 days)`. |
| Components / formula | Treatment and holdout eligible assignments and conversions; lift = treatment rate - holdout rate; estimated incremental customers = treatment eligible assignments * lift |
| CAC | Final-window campaign spend / estimated incremental customers |
| Null and edge treatment | Unknown campaigns excluded. Nonexposed treatment assignments remain in treatment. Negative or zero lift is reported, but incremental CAC is null when estimated incremental customers are not positive. In-progress windows are not final. |
| Aggregation class / rollups | Arm counts and spend are additive across compatible finalized windows. Rates, lift, incremental customers, and CAC are non-additive and recomputed from pooled arm totals. |
| SQL responsibility | Derive objective-specific outcome, maturity, ITT components, window boundaries, finality, compatible spend, and exact-window calculations |
| Intended DAX | `[Incremental Conversion Lift] = DIVIDE(SUM([treatment_conversions]), SUM([treatment_eligible])) - DIVIDE(SUM([holdout_conversions]), SUM([holdout_eligible]))`; `[Estimated Incremental Customers] = SUM([treatment_eligible]) * [Incremental Conversion Lift]`; `[Incremental CAC] = DIVIDE(SUM([spend]), [Estimated Incremental Customers])` |
| Semantic-model source / relationships | Arm and incremental reporting views to Campaign; measurement-window dimension or window-start Date relationship; do not relate the experiment fact directly to campaign daily at detail grain |

### 9. ITT 90-day incremental revenue, ROAS, contribution, and ROI

| Field | Contract |
|---|---|
| Business question | Did campaign assignment cause enough 90-day customer value to justify campaign spend? |
| Output grain | Campaign and finalized measurement window, with treatment/holdout value components retained |
| Eligible population | Known-campaign assignments whose complete 90-day value window is observable, kept in original assignment arm |
| Assignment value | Customer payments plus refunds and prorated service cost in `[assigned_at, assigned_at + 90 days)`, reduced to one assignment before arm aggregation. Nonconverters can have zero or negative Free-service contribution and remain in the denominator. |
| Incremental revenue | Treatment eligible * (mean treatment 90-day revenue - mean holdout 90-day revenue) |
| Incremental contribution | Treatment eligible * (mean treatment 90-day contribution - mean holdout 90-day contribution) |
| Incremental ROAS | Incremental revenue / campaign spend |
| Incremental ROI | `(incremental contribution - campaign spend) / campaign spend` |
| Null and edge treatment | Require a final window, positive spend, both arms, and mature value outcomes. Negative incremental values remain visible. Missing/imputed payment counts are exposed. No detail fact-to-fact join is allowed. |
| Aggregation class / rollups | Revenue, contribution, eligible assignments, and spend components are additive only after assignment/window reduction. Means, lifts, ROAS, and ROI are non-additive and recomputed from compatible pooled totals. |
| SQL responsibility | Build one assignment outcome row, aggregate each arm, aggregate daily spend independently to campaign/window, then join the two window-grain results |
| Intended DAX | `[Incremental ROAS 90D] = DIVIDE([Estimated Incremental Revenue 90D], SUM([spend]))`; `[Incremental ROI 90D] = DIVIDE([Estimated Incremental Contribution 90D] - SUM([spend]), SUM([spend]))`; percentage, two decimals |
| Semantic-model source / relationships | `reporting.vw_campaign_incremental_performance`; Campaign and measurement-window dimensions only; value and campaign-daily details remain separate facts |

### 10. Data quality

| Field | Contract |
|---|---|
| Business question | Which controlled source problems were detected, and did they affect selected analytics deliveries? |
| Output grain | Source table, issue code, selected-delivery flag; optional detected date |
| Components | Issue-record count and distinct impacted raw-row count |
| Denominator | Subject source-delivery count only when a clearly compatible denominator is supplied; no universal issue rate because one row can contain several issues |
| Null and edge treatment | Superseded and exact-duplicate deliveries remain reportable. Unknown campaign assignments are included here even though excluded from named-campaign metrics. |
| Aggregation class / rollups | Issue records are additive across disjoint categories. Distinct impacted rows and any rates are non-additive and recomputed. |
| SQL responsibility | Aggregate the centralized issue table without hiding selected-delivery status or distinct-row components |
| Intended DAX | Sum issue records. Use `DISTINCTCOUNT` on a stable composite raw-row key for impacted rows. Any rate uses an explicitly matched source denominator. |
| Semantic-model source / relationships | `reporting.vw_data_quality`; small Source Table and Issue Code dimensions; Date only when detected-date analysis is meaningful |

## Shared semantic-model measure catalog

| Measure | Format | Aggregation behavior |
|---|---|---|
| `Paid Churn Rate` | Percentage, 2 decimals | Sum churned opening customers / sum opening paid exposures |
| `Opening Paid Customers Snapshot` | Whole number | Latest visible monthly snapshot; never sum over time |
| `Paid Cohort Retention Rate` | Percentage, 2 decimals | Recompute for exactly one anniversary month number |
| `Payment Recovery Rate` | Percentage, 2 decimals | Sum recovered episodes / sum failed episodes |
| `Realized Contribution` | Currency | Sum additive customer-month contribution |
| `Expected 12M Paid CLV` | Currency | Compatible segment estimate or weighted average; never unweighted average |
| `CTR` | Percentage, 2 decimals | Sum clicks / sum impressions |
| `CPC` | Currency | Sum spend / sum clicks |
| `Platform CPA` | Currency | Sum spend / sum platform-attributed conversions |
| `Incremental Conversion Lift` | Percentage points, 2 decimals | Pooled treatment rate minus pooled holdout rate |
| `Estimated Incremental Customers` | Decimal, 1 decimal | Treatment eligible assignments multiplied by recomputed lift |
| `Incremental CAC` | Currency | Spend / positive estimated incremental customers |
| `Incremental ROAS 90D` | Decimal, 2 decimals | Estimated incremental revenue / spend |
| `Incremental ROI 90D` | Percentage, 2 decimals | (Estimated incremental contribution - spend) / spend |

These measures belong in one shared semantic model if Power BI is selected.
Report pages may format and display them but must not reimplement cohort,
eligibility, conversion, attribution, value-window, or experiment rules.

## Required validation queries and tests

Implementation is not accepted until tests prove all of the following:

1. Helper-view business keys are unique at their declared grains.
2. No customer has more than one opening paid state in a month.
3. Every churn numerator customer belongs to that month's opening denominator.
4. Pro/Master movement is absent from paid-logo churn and present in movement
   components.
5. Cohort checkpoints use exact customer anniversaries and exclude immature
   customers.
6. Multi-period churn and retention rates use summed components, not average
   rate columns.
7. Billing episodes cannot match one successful retry to several episode rows.
8. Payment-event counts and amounts reconcile before and after episode/value
   aggregation.
9. Customer-month revenue, service cost, and contribution reconcile to their
   independently aggregated sources.
10. Campaign daily rows are unique by campaign/date and preserve missing-value
    flags.
11. Assignment outcomes remain unique by `assignment_id`; treatment and
    holdout counts reconcile to mature known-campaign assignments.
12. Unknown campaign assignments appear in quality reporting and not in named
    campaign lift.
13. Incomplete 2026 Q3 is not marked final.
14. Treatment/holdout lift is recomputed from pooled group totals.
15. Campaign spend and assignment/value facts are independently aggregated to
    campaign/window before joining; dollars cannot multiply.
16. Every SQL artifact is schema-qualified, denominator-safe, replaceable, and
    free of destructive statements.
