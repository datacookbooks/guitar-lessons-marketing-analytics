# Reporting metric definitions

Last updated: 2026-08-29

## Purpose and status

This document is the authoritative contract for the implemented analytics
helper views, dashboard-facing reporting views, and canonical Power BI Import
semantic model. It began as a design-before-implementation contract and has now
been reconciled to the live SQL reporting layer and the implemented Power BI
model.

The live source profile was executed through a PostgreSQL read-only transaction
against the reviewed analytics snapshot. All core business sources cover
2024-01-01 through 2026-08-25. August 2026 and measurement window `2026_Q3` are
in progress and must not be presented as complete periods.

Current Power BI implementation status:

- The canonical model contains 16 Import tables. Automatic date/time is
  disabled and no automatic local date tables remain.
- The marked `Date` table contains 1,096 unique dates from 2024-01-01 through
  2026-12-31. Its cutoff flags identify 2026-08-25 as the latest available
  date and 2026-07-31 as the latest complete-month date.
- The dedicated `Paid Tenure Month` table contains months 1 through 12.
- The model has 22 many-to-one, single-direction relationships: 21 active and
  one inactive. It has no many-to-many or bidirectional relationships.
- The `Shared Measures` table contains 15 explicit, visible DAX measures. All
  have reviewed folders, formats, descriptions, and metadata state `1`
  (`Ready`).
- The measure calculations and important additive components have been
  reconciled to the reviewed reporting snapshot. Final report-facing field
  naming and formatting remains a semantic-model presentation task, not a
  change to the metric contracts below.

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
| Intended DAX / implemented Power BI behavior | `[Paid Churn Rate]` divides summed churned customers by summed opening exposures. `[Opening Paid Customers Snapshot]` uses only the latest visible `month_start`. See the final shared-measure catalog for formats and descriptions. |
| Semantic-model source / relationships | `Monthly Paid Movement` from `reporting.vw_monthly_paid_movement`; active many-to-one relationships to `Date` by `month_start`, `Plan` by `opening_plan_id`, and `Campaign` by `first_campaign_id`. The alternate relationship from `paid_cohort_month` to `Date[Date]` is inactive. Customer segments remain attributes on the reporting table. |

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
| Intended DAX / current Power BI behavior | Upgrade, downgrade, and reactivation components remain additive report fields. No separate event-count measures are currently implemented. The explicit opening snapshot measure uses the latest visible month. Add any future distinct-customer measure from an appropriate stable helper key rather than summing monthly distinct counts. |
| Semantic-model source / relationships | The implemented `Monthly Paid Movement` view relates actively to `Date` by `month_start` and to `Plan` by `opening_plan_id`. Upgrade, downgrade, and reactivation event components remain additive reporting fields; no unimplemented from-plan/to-plan relationship should be assumed. |

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
| Intended DAX / implemented Power BI behavior | `[Paid Cohort Retention Rate]` requires exactly one `Paid Tenure Month`, then divides summed retained customers by summed eligible customers; otherwise it returns blank. |
| Semantic-model source / relationships | `Paid Cohort Retention` from `reporting.vw_paid_cohort_retention`; active relationships to `Date` by `paid_cohort_month`, `Plan` by `initial_paid_plan_id`, `Campaign` by `first_campaign_id`, and `Paid Tenure Month` by `month_number`. |

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
| Intended DAX / implemented Power BI behavior | `[Payment Recovery Rate]` divides summed recovered episodes by summed failed billing episodes. |
| Semantic-model source / relationships | `Payment Recovery` from `reporting.vw_payment_recovery`; active relationships to `Date` by `failure_month`, `Plan` by `plan_id`, and `Campaign` by `first_campaign_id`. Approved customer segments remain attributes on the reporting table. |

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
| Intended DAX / implemented Power BI behavior | `[Realized Contribution]` sums the additive customer-month contribution. A separate `Customers With Imputed Value` measure is not currently implemented; if added later, it must use a stable helper key and must not sum monthly distinct counts. |
| Semantic-model source / relationships | `Customer Value` from `reporting.vw_customer_value`; active relationships to `Date` by `month_start` and `Campaign` by `first_campaign_id`. `customer_id` remains a hidden reporting-grain key; no separate Customer dimension is currently imported. Do not directly join campaign daily facts. |

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
| Intended DAX / implemented Power BI behavior | `[Expected 12M Paid CLV]` uses the compatible SQL estimate and a `new_paid_weight` weighted average at broader compatible selections. It never sums or unweighted-averages segment CLVs. |
| Semantic-model source / relationships | `Expected 12M Paid CLV` from `reporting.vw_expected_12m_paid_clv`; active relationship to `Plan` by `initial_paid_plan_id`. Acquisition channel and segment level remain attributes on the table. `Paid CLV Monthly Component` relates separately to `Plan` and `Paid Tenure Month` and is hidden from ordinary report construction because its raw rates and margins are not safely additive. |

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
| Intended DAX / implemented Power BI behavior | `[CTR]`, `[CPC]`, and `[Platform CPA]` recompute the ratios from summed components and return blank when required selected components are missing or the relevant denominator is zero. |
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
| Intended DAX / implemented Power BI behavior | `[Incremental Conversion Lift]` recomputes pooled treatment minus pooled holdout conversion rates across finalized 30-day windows. `[Estimated Incremental Customers]` applies that lift to pooled treatment eligibility. `[Incremental CAC]` divides spend by positive incremental customers and returns blank when finalized selected windows contain missing spend. |
| Semantic-model source / relationships | `Campaign Experiment Arm` and `Campaign Incremental Performance` both relate actively to `Campaign` by `campaign_id` and to `Date` by `measurement_window_start`. No separate measurement-window dimension is implemented. Do not relate either experiment table directly to campaign daily detail. |

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
| Intended DAX / implemented Power BI behavior | `[Incremental ROAS 90D]` recomputes incremental revenue from pooled treatment/holdout 90-day components and divides by spend. `[Incremental ROI 90D]` analogously recomputes incremental contribution, subtracts spend, and divides by spend. Both require finalized 90-day windows and return blank when required spend is missing or nonpositive. |
| Semantic-model source / relationships | `Campaign Incremental Performance` from `reporting.vw_campaign_incremental_performance`; active relationships to `Campaign` by `campaign_id` and `Date` by `measurement_window_start`. Value and campaign-daily details remain separate facts. |

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
| Intended DAX / current Power BI behavior | No explicit data-quality measures are currently implemented. `issue_records` is additive across disjoint categories. `impacted_raw_rows` must not be summed across potentially overlapping issue categories; any future distinct-row measure requires a stable raw-row key at a compatible grain. No universal issue rate is allowed. |
| Semantic-model source / relationships | `Data Quality` from `reporting.vw_data_quality` is currently a standalone reporting-grain table. Source table, issue code, selected-delivery flag, and detection timestamps remain report-facing attributes; no separate Source Table, Issue Code, or Date relationship is implemented. |

## Final Power BI shared-measure catalog

The measures below are implemented in the `Shared Measures` table. The exact
DAX source is stored in the canonical PBIX; this document records the
authoritative calculation behavior that the expression must preserve. Source
columns may be hidden from report construction without affecting these
measures.

| Measure | Display folder | Exact format string | Implemented calculation contract |
|---|---|---|---|
| `Data Through Date` | — | `MMM d, yyyy` | Latest imported `Reporting Cutoff[data_through_date]`; communicates snapshot currency and is not the current system date. |
| `Paid Churn Rate` | Customer Lifecycle | `0.00%;-0.00%;0.00%` | Sum churned paid customers divided by sum opening paid-customer exposures in the current context. Never average the SQL rate column. |
| `Opening Paid Customers Snapshot` | Customer Lifecycle | `#,0` | Sum opening paid customers only at the latest visible `month_start`; never sum snapshots across selected months. |
| `Paid Cohort Retention Rate` | Customer Lifecycle | `0.00%;-0.00%;0.00%` | When exactly one `Paid Tenure Month` is selected, sum retained customers divided by sum eligible customers. Return blank when zero or multiple tenure checkpoints are combined. |
| `Payment Recovery Rate` | Billing | `0.00%;-0.00%;0.00%` | Sum recovered billing episodes divided by sum failed billing episodes. Several failed attempts within one episode remain one denominator episode. |
| `Expected 12M Paid CLV` | Customer Value | `$#,0.00;($#,0.00);$#,0.00` | Weighted average of the compatible SQL CLV estimates using `new_paid_weight`. Use the appropriate plan or plan-and-channel segment grain and never take an unweighted average or sum of segment CLVs. |
| `Realized Contribution` | Customer Value | `$#,0.00;($#,0.00);$#,0.00` | Sum additive customer-month realized contribution after payment/refund handling and prorated service cost. |
| `CTR` | Campaign Delivery | `0.00%;-0.00%;0.00%` | Sum clicks divided by sum impressions. Return blank when selected rows contain missing required click data or the denominator is zero. |
| `CPC` | Campaign Delivery | `$#,0.00;($#,0.00);$#,0.00` | Sum spend divided by sum clicks. Return blank when required click or spend data is missing or clicks are zero. |
| `Platform CPA` | Campaign Delivery | `$#,0.00;($#,0.00);$#,0.00` | Sum spend divided by sum platform-attributed conversions. Return blank when required spend is missing or attributed conversions are zero. This is descriptive attribution, not causal lift. |
| `Incremental Conversion Lift` | Campaign Incrementality | `0.00%;-0.00%;0.00%` | For finalized 30-day ITT windows, recompute the pooled treatment conversion rate and subtract the pooled holdout conversion rate. |
| `Estimated Incremental Customers` | Campaign Incrementality | `0.0` | Pooled treatment-eligible assignments multiplied by recomputed incremental conversion lift for finalized 30-day ITT windows. Negative results remain visible. |
| `Incremental CAC` | Campaign Incrementality | `$#,0.00;($#,0.00);$#,0.00` | Final-window campaign spend divided by positive estimated incremental customers. Return blank if a selected finalized 30-day window has missing spend or incremental customers are not positive. |
| `Incremental ROAS 90D` | Campaign Incrementality | `0.00` | Recomputed estimated incremental 90-day revenue divided by campaign spend across compatible finalized 90-day ITT windows. Return blank if required spend is missing or nonpositive. |
| `Incremental ROI 90D` | Campaign Incrementality | `0.00%;-0.00%;0.00%` | Recomputed estimated incremental 90-day contribution minus campaign spend, divided by campaign spend, across compatible finalized 90-day ITT windows. Return blank if required spend is missing or nonpositive. |

### Final measure descriptions

| Measure | Description stored in the semantic model |
|---|---|
| `Data Through Date` | Latest source date included in the imported reporting snapshot. Conveys how recent the data in the report is. |
| `Paid Churn Rate` | Percentage of customers paying at the start of the selected month or months who left paid service, either by cancelling or by switching to the free tier. Recomputed from total churned customers and opening paid-customer exposures; excludes changes from one paid tier to another. Results for incomplete months are provisional and should not be treated as final. |
| `Opening Paid Customers Snapshot` | Number of paid customers at the opening of the latest visible month. Uses the latest selected monthly snapshot rather than summing snapshots over time. |
| `Paid Cohort Retention Rate` | Percentage of customers still on a paid plan at the selected number of months after they first became paying customers. Only customers who have reached that checkpoint are included. Requires exactly one tenure month and otherwise returns blank. |
| `Payment Recovery Rate` | Percentage of failed billing episodes followed by a successful charge within seven days. Multiple failed attempts within one billing episode count once. |
| `Expected 12M Paid CLV` | Estimated contribution value a new paying customer is expected to generate during their first 12 months, based on observed customer retention and plan margins. This is a forecast, not actual realized value. |
| `Realized Contribution` | Actual contribution value generated by customers to date, calculated as successful payment revenue minus refunds and estimated service costs, which are prorated. Missing payment amounts are estimated using the applicable plan price when allowed by the documented data rules. |
| `CTR` | Clicks divided by impressions in the current filter context. Returns blank when required components are missing or impressions are zero. |
| `CPC` | Campaign spend divided by clicks in the current filter context. Returns blank when required components are missing or clicks are zero. |
| `Platform CPA` | Average campaign spend per conversion credited by the marketing platform. Calculated as total spend divided by platform-attributed conversions. This reflects the platform's attribution and does not measure how many conversions the campaign actually caused. |
| `Incremental Conversion Lift` | Estimated change in the 30-day conversion rate caused by the campaign. It compares customers assigned to receive the campaign with a holdout group that was not assigned to receive it. For the current selection, all eligible customers in each group are combined before the two conversion rates are calculated and compared. |
| `Estimated Incremental Customers` | Estimated number of additional customers who converted because of the campaign, beyond the number expected to convert without it. Calculated by applying the difference between the campaign and holdout conversion rates to the number of customers assigned to the campaign. The result can be negative if the campaign group performed worse than the holdout group. |
| `Incremental CAC` | Average campaign spend for each additional customer estimated to have converted because of the campaign. Calculated as campaign spend divided by estimated incremental customers. Returns blank when the estimated number of incremental customers is zero or negative, or when required spending data is missing. |
| `Incremental ROAS 90D` | Estimated additional revenue generated by the campaign during the 90 days after assignment, divided by campaign spend. For example, a value of 1.50 means the campaign generated an estimated $1.50 in additional revenue for every $1.00 spent. This measures revenue rather than profit. |
| `Incremental ROI 90D` | Estimated return after campaign spend, based on the additional contribution value generated during the 90 days after assignment. Contribution value subtracts estimated variable service costs from revenue; campaign spend is then subtracted before calculating the return percentage. For example, 25% means the campaign produced an estimated $0.25 beyond its cost for every $1.00 spent. |

These measures are the approved shared semantic-model interface. Report pages
may display and filter them but must not reimplement cohort eligibility,
conversion, attribution, value-window, churn, CLV, experiment, or data-quality
rules independently.

## Implemented Power BI validation evidence

All measure metadata returned state `1` (`Ready`). The following validations
were run against the saved Import snapshot with data through 2026-08-25:

| Subject | Reconciliation result |
|---|---|
| Paid churn | 41,458 opening paid-customer exposures; 2,664 churned customers; recomputed rate 6.4257803%. |
| Opening snapshot | Latest visible snapshot month 2026-08-01; direct value 2,518; measure value 2,518; difference zero. |
| Paid retention | Combined checkpoints: 45,261 eligible and 29,782 retained; rate correctly blank without one checkpoint. Months 1, 3, 6, and 12 matched direct rates exactly at 90.4238%, 74.7111%, 61.7419%, and 47.8042%. |
| Payment recovery | 4,109 failed billing episodes; 2,969 recovered episodes; recovery rate 72.2560%. |
| Expected CLV | Portfolio measure and direct plan-weighted result both $200.3417087; every plan/channel segment matched, aside from negligible floating-point representation. |
| Realized contribution | 164,574 customer-month rows; $1,982,066 effective charge revenue; -$34,981 effective refunds; $1,023,821.21 prorated service cost; $923,263.79 realized contribution. Difference from the direct calculation was approximately `-1.16e-10`. |
| Campaign delivery | 2,904 daily rows, 25 missing-click rows, and ten missing-spend rows. CTR 4.1518577%, CPC $1.8842516, and Platform CPA $49.9813179 each matched the direct calculation with zero difference. |
| 30-day lift | 29 finalized windows; 69,117 treatment eligible; 12,303 holdout eligible; 5,014 treatment conversions; 718 holdout conversions; lift 1.4183908 percentage points; 980.3492 estimated incremental customers. |
| Incremental CAC | The all-final-window selection correctly returned blank because it contained seven missing spend days. Across 23 spend-complete final windows, $199,974.44 spend / 876.2009 incremental customers = $228.22898 CAC, with zero difference. |
| 90-day ROAS and ROI | All 26 final windows correctly returned blank because six windows contained seven missing spend days. Across 20 spend-complete final windows, ROAS was 0.1498596 and ROI was -91.09694%, each matching the direct calculation with zero difference. |

## Required validation queries and tests

The SQL helper/reporting implementation and the shared-measure calculations
were accepted only after tests and live reconciliation proved the following:

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

## Remaining semantic-model acceptance work

The metric logic itself is implemented and reconciled. Before declaring the
entire semantic-model milestone complete:

1. Finish friendly field names, display formats, and default summarization on
   the remaining visible reporting fields.
2. Rerun the column inventory and confirm technical keys, duplicated dimension
   labels, unsafe SQL rates, lineage fields, `Reporting Cutoff`, and raw paid-
   CLV components remain hidden from ordinary report construction.
3. Confirm the final topology remains 16 Import tables, 22 relationships, 21
   active relationships, one inactive relationship, and no many-to-many or
   bidirectional relationships.
4. Test representative Date/Campaign, Date/Plan, tenure/retention, and complete-
   versus-missing-spend slicer combinations for blank leakage, duplicated
   totals, invalid cross-filtering, and guardrail behavior.
5. Save the validated canonical PBIX and a separate post-validation backup
   outside Git, then capture the final model diagram and reconciliation
   evidence for repository documentation.
