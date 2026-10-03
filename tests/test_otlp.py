from bisect import bisect_left
from math import pow

import pytest

from cardinality_lab.otlp import (
    CUMULATIVE,
    DELTA,
    EXPLICIT_BOUNDS,
    EXPONENTIAL_OFFSET,
    EXPONENTIAL_SCALE,
    METRIC_SPECS,
    QUANTILES,
    SCOPE_NAME,
    SPECS_BY_KEY,
    MetricSpec,
    build_metric,
    build_request,
    datapoint_count,
    key_values,
)
from cardinality_lab.workloads import WORKLOADS_BY_NAME

START = 1_700_000_000_000_000_000
NOW = 1_700_000_010_000_000_000
WORKLOAD = WORKLOADS_BY_NAME["single-offender"]


def metric_for(spec: MetricSpec, count: int = 5):
    return build_metric(spec, WORKLOAD.attribute_sets(0, count), START, NOW)


def data_points(metric):
    return getattr(metric, metric.WhichOneof("data")).data_points


def test_key_values_are_sorted_strings() -> None:
    pairs = key_values({"b": "2", "a": "1"})
    assert [pair.key for pair in pairs] == ["a", "b"]
    assert [pair.value.string_value for pair in pairs] == ["1", "2"]


@pytest.mark.parametrize("spec", METRIC_SPECS, ids=lambda spec: spec.key)
def test_every_spec_encodes_its_oneof_field(spec: MetricSpec) -> None:
    metric = metric_for(spec)
    assert metric.WhichOneof("data") == spec.field
    assert metric.name == f"lab.bench.{spec.key}"
    assert len(data_points(metric)) == 5


@pytest.mark.parametrize("spec", METRIC_SPECS, ids=lambda spec: spec.key)
def test_every_datapoint_carries_timestamps_and_attributes(spec: MetricSpec) -> None:
    for index, point in enumerate(data_points(metric_for(spec))):
        assert point.start_time_unix_nano == START
        assert point.time_unix_nano == NOW
        attributes = {pair.key: pair.value.string_value for pair in point.attributes}
        assert attributes == dict(WORKLOAD.builder(index))


@pytest.mark.parametrize("spec", METRIC_SPECS, ids=lambda spec: spec.key)
def test_attribute_sets_are_unique_per_datapoint(spec: MetricSpec) -> None:
    identities = {
        tuple(sorted((pair.key, pair.value.string_value) for pair in point.attributes))
        for point in data_points(metric_for(spec, 50))
    }
    assert len(identities) == 50


@pytest.mark.parametrize(
    ("key", "temporality"),
    [
        ("delta_sum", DELTA),
        ("cumulative_sum", CUMULATIVE),
        ("delta_histogram", DELTA),
        ("cumulative_histogram", CUMULATIVE),
        ("delta_exponential_histogram", DELTA),
        ("cumulative_exponential_histogram", CUMULATIVE),
    ],
)
def test_temporality_is_encoded(key: str, temporality: int) -> None:
    spec = SPECS_BY_KEY[key]
    body = getattr(metric_for(spec), spec.field)
    assert body.aggregation_temporality == temporality


def test_sum_monotonicity_is_encoded() -> None:
    assert metric_for(SPECS_BY_KEY["delta_sum"]).sum.is_monotonic is True
    assert metric_for(SPECS_BY_KEY["cumulative_sum"]).sum.is_monotonic is True


@pytest.mark.parametrize("key", ["gauge", "summary"])
def test_gauge_and_summary_have_no_temporality_field(key: str) -> None:
    spec = SPECS_BY_KEY[key]
    body = getattr(metric_for(spec), spec.field)
    assert "aggregation_temporality" not in {
        field.name for field in body.DESCRIPTOR.fields
    }


@pytest.mark.parametrize("key", ["delta_histogram", "cumulative_histogram"])
def test_explicit_bucket_histograms_are_consistent(key: str) -> None:
    for point in data_points(metric_for(SPECS_BY_KEY[key])):
        assert tuple(point.explicit_bounds) == EXPLICIT_BOUNDS
        assert len(point.bucket_counts) == len(EXPLICIT_BOUNDS) + 1
        assert sum(point.bucket_counts) == point.count == 3
        assert point.sum == pytest.approx(3 * point.min)
        populated = list(point.bucket_counts).index(3)
        assert populated == bisect_left(EXPLICIT_BOUNDS, point.min)


@pytest.mark.parametrize(
    "key",
    ["delta_exponential_histogram", "cumulative_exponential_histogram"],
)
def test_exponential_histograms_carry_scale_and_buckets(key: str) -> None:
    for point in data_points(metric_for(SPECS_BY_KEY[key])):
        assert point.scale == EXPONENTIAL_SCALE
        assert point.positive.offset == EXPONENTIAL_OFFSET
        assert point.zero_count == 0
        assert sum(point.positive.bucket_counts) == point.count == 3
        populated = list(point.positive.bucket_counts).index(3)
        bucket_index = point.positive.offset + populated
        base = pow(2.0, pow(2.0, -point.scale))
        assert pow(base, bucket_index) < point.min
        assert point.max <= pow(base, bucket_index + 1)
        assert point.sum == pytest.approx(3 * point.min)


def test_summary_carries_every_quantile() -> None:
    for point in data_points(metric_for(SPECS_BY_KEY["summary"])):
        assert tuple(value.quantile for value in point.quantile_values) == QUANTILES
        assert point.count == 3
        assert all(value.value > 0 for value in point.quantile_values)


def test_prometheus_family_names_follow_the_translation_strategy() -> None:
    assert SPECS_BY_KEY["delta_sum"].prometheus_families() == (
        "lab_bench_delta_sum_total",
    )
    assert SPECS_BY_KEY["delta_histogram"].prometheus_families() == (
        "lab_bench_delta_histogram_bucket",
        "lab_bench_delta_histogram_sum",
        "lab_bench_delta_histogram_count",
    )
    assert SPECS_BY_KEY["gauge"].identity_family == "lab_bench_gauge"
    assert SPECS_BY_KEY["summary"].identity_family == "lab_bench_summary_count"
    assert SPECS_BY_KEY["summary"].prometheus_families() == (
        "lab_bench_summary",
        "lab_bench_summary_sum",
        "lab_bench_summary_count",
    )


def test_exponential_histograms_are_exposed_as_classic_buckets() -> None:
    for key in ("delta_exponential_histogram", "cumulative_exponential_histogram"):
        spec = SPECS_BY_KEY[key]
        assert spec.prometheus_shape == "classic histogram"
        assert spec.identity_family == f"lab_bench_{key}_count"
        assert spec.prometheus_families() == (
            f"lab_bench_{key}_bucket",
            f"lab_bench_{key}_sum",
            f"lab_bench_{key}_count",
        )


def test_only_delta_sum_and_gauge_claim_reaggregation_support() -> None:
    supported = {spec.key for spec in METRIC_SPECS if spec.reaggregation_supported}
    assert supported == {"gauge", "delta_sum"}


def test_build_request_carries_resource_scope_and_counts() -> None:
    metrics = [metric_for(spec, 4) for spec in METRIC_SPECS]
    request = build_request(metrics, {"service.name": "bench", "scenario": "matrix"})
    resource_metrics = request.resource_metrics[0]
    attributes = {
        pair.key: pair.value.string_value
        for pair in resource_metrics.resource.attributes
    }
    assert attributes == {"service.name": "bench", "scenario": "matrix"}
    assert resource_metrics.scope_metrics[0].scope.name == SCOPE_NAME
    assert datapoint_count(request) == 4 * len(METRIC_SPECS)
