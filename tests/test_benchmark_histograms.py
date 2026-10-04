import pytest

from cardinality_lab.benchmark import (
    HISTOGRAM_STRATEGIES,
    SUITES,
    build_cases,
    expected_points,
    histogram_cases,
    questions_for,
    validity,
)
from cardinality_lab.otlp import _histogram_point
from cardinality_lab.prometheus import COLLECTOR_COUNTERS
from cardinality_lab.sdk_emitter import OBSERVATIONS_PER_IDENTITY
from cardinality_lab.stack import STRATEGIES_BY_NAME

BASE = "lab_bench_delta_histogram"


def counters(exported: float | None) -> dict[str, float | None]:
    values = dict.fromkeys(COLLECTOR_COUNTERS)
    values["otelcol_receiver_accepted_metric_points"] = 200.0
    values["otelcol_receiver_refused_metric_points"] = 0.0
    values["otelcol_exporter_sent_metric_points"] = exported
    return values


def test_the_suite_is_selectable_and_carries_every_strategy() -> None:
    assert "histograms" in SUITES
    cases = build_cases(("histograms",), epoch_seconds=10, include_large_scale=False)
    assert len(cases) == 1
    assert cases[0].strategies == HISTOGRAM_STRATEGIES
    assert cases[0].modes(("baseline",)) == HISTOGRAM_STRATEGIES


@pytest.mark.parametrize("name", HISTOGRAM_STRATEGIES)
def test_every_histogram_strategy_exists_in_the_catalog(name: str) -> None:
    assert name in STRATEGIES_BY_NAME


def test_other_suites_still_follow_the_requested_modes() -> None:
    cases = build_cases(("scale",), epoch_seconds=10, include_large_scale=False)
    assert cases[0].modes(("baseline", "tag_only")) == ("baseline", "tag_only")


def test_every_question_has_a_classic_and_a_native_form() -> None:
    questions = questions_for(BASE)
    assert [question.name for question in questions] == [
        "total_requests",
        "p95_by_method",
        "requests_by_status",
        "p95_for_one_user",
    ]
    for question in questions:
        assert question.expression("classic") == question.classic
        assert question.expression("native") == question.native
        assert BASE in question.classic
        assert "histogram_count" in question.native or "histogram_quantile" in (
            question.native
        )


def test_the_per_user_question_names_one_user() -> None:
    question = next(
        item for item in questions_for(BASE) if item.name == "p95_for_one_user"
    )
    assert 'user_id="user-000042"' in question.classic
    assert 'user_id="user-000042"' in question.native


def test_an_sdk_run_waits_for_a_single_accepted_point() -> None:
    case = histogram_cases()[0]
    assert expected_points(case, {"sent_datapoints": None}) == 1.0
    assert expected_points(case, {"sent_datapoints": 200}) == 200.0


def test_an_sdk_run_is_valid_without_a_sent_datapoint_count() -> None:
    case = histogram_cases()[0]
    sending = {
        "sent_datapoints": None,
        "rejected_datapoints": 0,
        "plan_duration_seconds": 0.0,
        "schedule_lag_seconds": 0.0,
    }
    result = validity(case, sending, counters(20.0))
    assert result["ok"] is True


def test_only_the_filter_strategy_is_expected_to_export_nothing() -> None:
    silent = [
        name for name in HISTOGRAM_STRATEGIES if not STRATEGIES_BY_NAME[name].exports
    ]
    assert silent == ["filter"]


def test_both_emitters_record_three_observations_per_identity() -> None:
    assert OBSERVATIONS_PER_IDENTITY == 3
    point = _histogram_point({"user.id": "user-000000"}, 0, 1, 2)
    assert point.count == 3
    assert sum(point.bucket_counts) == 3


def test_a_run_that_reached_no_exporter_is_invalid_unless_that_is_the_point() -> None:
    case = histogram_cases()[0]
    sending = {
        "sent_datapoints": 200,
        "rejected_datapoints": 0,
        "plan_duration_seconds": 0.0,
        "schedule_lag_seconds": 0.0,
    }
    assert validity(case, sending, counters(0.0))["ok"] is False
    assert validity(case, sending, counters(0.0), expect_export=False)["ok"] is True
    assert validity(case, sending, counters(None), expect_export=False)["ok"] is True
