import pytest
from opentelemetry.sdk.metrics.view import (
    ExplicitBucketHistogramAggregation,
    ExponentialBucketHistogramAggregation,
)

from cardinality_lab.otlp import EXPLICIT_BOUNDS, SPECS_BY_KEY, build_metric
from cardinality_lab.sdk_emitter import (
    OBSERVATIONS_PER_IDENTITY,
    aggregation_for,
    observed_value,
    view_for,
)
from cardinality_lab.workloads import WORKLOADS_BY_NAME

METRIC = "lab.bench.delta_histogram"


def test_explicit_aggregation_uses_the_otlp_encoder_bounds() -> None:
    aggregation = aggregation_for("explicit")
    assert isinstance(aggregation, ExplicitBucketHistogramAggregation)
    assert aggregation._boundaries == list(EXPLICIT_BOUNDS)


def test_exponential_aggregation_keeps_the_sdk_defaults() -> None:
    aggregation = aggregation_for("exponential")
    assert isinstance(aggregation, ExponentialBucketHistogramAggregation)
    assert aggregation._max_size == 160
    assert aggregation._max_scale == 20


def test_unknown_aggregation_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown SDK aggregation"):
        aggregation_for("quantile")


def test_a_view_without_attribute_keys_keeps_every_attribute() -> None:
    view = view_for(METRIC, "explicit", None)
    assert view._attribute_keys is None


def test_a_view_with_attribute_keys_drops_the_unbounded_one() -> None:
    kept = ("environment", "region", "http.method", "http.status_code")
    view = view_for(METRIC, "explicit", kept)
    assert view._attribute_keys == set(kept)
    assert "user.id" not in view._attribute_keys


def test_the_sdk_path_records_the_values_the_otlp_encoder_sends() -> None:
    workload = WORKLOADS_BY_NAME["histogram-user-id"]
    attribute_sets = workload.attribute_sets(0, 4)
    metric = build_metric(SPECS_BY_KEY["delta_histogram"], attribute_sets, 1, 2)
    for index, point in enumerate(metric.histogram.data_points):
        assert point.count == OBSERVATIONS_PER_IDENTITY
        assert point.sum == OBSERVATIONS_PER_IDENTITY * observed_value(index)
