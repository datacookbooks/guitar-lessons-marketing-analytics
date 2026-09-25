from __future__ import annotations

import json
from pathlib import Path

import pytest

from extract_load.pipeline_validation import (
    PipelineValidationError,
    StagingTableState,
    validate_analytics_invariants,
    validate_reporting_invariants,
    validate_staging_state,
)
from extract_load.staging_rows import SOURCE_COLUMNS


def _valid_staging_state() -> dict[str, StagingTableState]:
    return {
        table_name: StagingTableState(
            row_count=10,
            distinct_raw_row_ids=10,
            rows_without_manifest=0,
            watermark=10,
            maximum_loaded_cursor=10,
            manifest_objects=1,
            manifest_source_rows=10,
        )
        for table_name in SOURCE_COLUMNS
    }


def test_staging_invariants_accept_consistent_growth() -> None:
    validate_staging_state(_valid_staging_state())


def test_staging_invariants_reject_watermark_drift() -> None:
    state = _valid_staging_state()
    state["dim_customer"] = StagingTableState(
        row_count=10,
        distinct_raw_row_ids=10,
        rows_without_manifest=0,
        watermark=11,
        maximum_loaded_cursor=10,
        manifest_objects=1,
        manifest_source_rows=10,
    )

    with pytest.raises(PipelineValidationError, match="watermark"):
        validate_staging_state(state)


def test_analytics_invariants_accept_nondecreasing_counts() -> None:
    before = {"dim_customer": 10, "data_quality_issue": 2}
    after = {"dim_customer": 11, "data_quality_issue": 3}

    validate_analytics_invariants(
        before_counts=before,
        after_counts=after,
        lineage_violations={"dim_customer": 0},
        unknown_campaign_valid=True,
    )


def test_analytics_invariants_reject_missing_lineage() -> None:
    counts = {"dim_customer": 10, "data_quality_issue": 2}

    with pytest.raises(PipelineValidationError, match="missing staging lineage"):
        validate_analytics_invariants(
            before_counts=counts,
            after_counts=counts,
            lineage_violations={"dim_customer": 1},
            unknown_campaign_valid=True,
        )


def _valid_reporting_values() -> dict[str, int]:
    return {
        "new_paid_customers": 5,
        "movement_opening_paid": 20,
        "movement_churned_paid": 2,
        "retention_eligible": 10,
        "retention_retained": 8,
        "failed_billing_episodes": 4,
        "recovered_episodes": 3,
        "campaign_daily_rows": 7,
        "incremental_windows": 4,
        "final_90d_windows": 2,
        "quality_issues": 3,
        "sufficient_plan_clv_rows": 2,
    }


def test_reporting_invariants_accept_valid_subsets() -> None:
    validate_reporting_invariants(
        analytics_counts={
            "dim_customer": 10,
            "fact_campaign_daily": 7,
            "data_quality_issue": 3,
            "dim_plan": 3,
        },
        reporting_values=_valid_reporting_values(),
    )


def test_reporting_invariants_reject_impossible_recovery_count() -> None:
    values = _valid_reporting_values()
    values["recovered_episodes"] = 5

    with pytest.raises(PipelineValidationError, match="recovered episodes"):
        validate_reporting_invariants(
            analytics_counts={
                "dim_customer": 10,
                "fact_campaign_daily": 7,
                "data_quality_issue": 3,
                "dim_plan": 3,
            },
            reporting_values=values,
        )


def test_reviewed_baseline_remains_versioned_regression_evidence() -> None:
    fixture_path = (
        Path(__file__).parent
        / "fixtures"
        / "reviewed_baseline_2026_09_19.json"
    )
    baseline = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert baseline["analytics_counts"]["dim_customer"] == 11_643
    assert baseline["analytics_counts"]["data_quality_issue"] == 9_454
    assert baseline["reporting_reconciliation"]["quality_issues"] == 9_454
