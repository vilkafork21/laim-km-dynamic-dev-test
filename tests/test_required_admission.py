import pytest

from test_scored_output_contract import _flat_frame, _metric
from main import main


@pytest.mark.parametrize("calibration", [None, {}, {"admission_status": "unknown"}])
def test_unverified_judge_cannot_produce_green_km(calibration):
    result = main(
        monitoring_metric=_metric(), scored_df=_flat_frame(100, 0), acc_auto=1.0,
        assessment_result={"status": "computed", "calibration_metrics": calibration},
    )["all_results"]
    assert result["status"] == "not_computable"
    assert result["color"] == "gray"
    assert result["reason_code"] == "judge_not_admitted"


def test_missing_assessment_cannot_produce_green_km():
    result = main(
        monitoring_metric=_metric(), scored_df=_flat_frame(100, 0), acc_auto=1.0,
    )["all_results"]
    assert result["status"] == "not_computable"
    assert result["reason_code"] == "judge_not_admitted"
