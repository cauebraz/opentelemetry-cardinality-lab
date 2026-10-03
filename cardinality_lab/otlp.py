import time
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import pow

import grpc
from opentelemetry.proto.collector.metrics.v1 import (
    metrics_service_pb2,
    metrics_service_pb2_grpc,
)
from opentelemetry.proto.common.v1 import common_pb2
from opentelemetry.proto.metrics.v1 import metrics_pb2
from opentelemetry.proto.resource.v1 import resource_pb2

DELTA = metrics_pb2.AggregationTemporality.AGGREGATION_TEMPORALITY_DELTA
CUMULATIVE = metrics_pb2.AggregationTemporality.AGGREGATION_TEMPORALITY_CUMULATIVE

EXPLICIT_BOUNDS = (5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0)
QUANTILES = (0.5, 0.95, 0.99)
EXPONENTIAL_SCALE = 3
EXPONENTIAL_OFFSET = 0
EXPONENTIAL_BUCKETS = 4
SCOPE_NAME = "cardinality_lab.bench"


@dataclass(frozen=True)
class MetricSpec:
    """One OTLP metric shape, with how the Guardian and Prometheus treat it."""

    key: str
    field: str
    temporality: int
    monotonic: bool
    reaggregation_supported: bool
    prometheus_suffixes: tuple[str, ...]
    identity_suffix: str
    prometheus_shape: str

    @property
    def metric_name(self) -> str:
        return f"lab.bench.{self.key}"

    @property
    def prometheus_base(self) -> str:
        return self.metric_name.replace(".", "_")

    def prometheus_families(self) -> tuple[str, ...]:
        return tuple(
            f"{self.prometheus_base}{suffix}" for suffix in self.prometheus_suffixes
        )

    @property
    def identity_family(self) -> str:
        return f"{self.prometheus_base}{self.identity_suffix}"


METRIC_SPECS: tuple[MetricSpec, ...] = (
    MetricSpec(
        key="gauge",
        field="gauge",
        temporality=metrics_pb2.AggregationTemporality.AGGREGATION_TEMPORALITY_UNSPECIFIED,
        monotonic=False,
        reaggregation_supported=True,
        prometheus_suffixes=("",),
        prometheus_shape="gauge",
        identity_suffix="",
    ),
    MetricSpec(
        key="delta_sum",
        field="sum",
        temporality=DELTA,
        monotonic=True,
        reaggregation_supported=True,
        prometheus_suffixes=("_total",),
        prometheus_shape="counter",
        identity_suffix="_total",
    ),
    MetricSpec(
        key="cumulative_sum",
        field="sum",
        temporality=CUMULATIVE,
        monotonic=True,
        reaggregation_supported=False,
        prometheus_suffixes=("_total",),
        prometheus_shape="counter",
        identity_suffix="_total",
    ),
    MetricSpec(
        key="delta_histogram",
        field="histogram",
        temporality=DELTA,
        monotonic=False,
        reaggregation_supported=False,
        prometheus_suffixes=("_bucket", "_sum", "_count"),
        prometheus_shape="classic histogram",
        identity_suffix="_count",
    ),
    MetricSpec(
        key="cumulative_histogram",
        field="histogram",
        temporality=CUMULATIVE,
        monotonic=False,
        reaggregation_supported=False,
        prometheus_suffixes=("_bucket", "_sum", "_count"),
        prometheus_shape="classic histogram",
        identity_suffix="_count",
    ),
    MetricSpec(
        key="delta_exponential_histogram",
        field="exponential_histogram",
        temporality=DELTA,
        monotonic=False,
        reaggregation_supported=False,
        prometheus_suffixes=("_bucket", "_sum", "_count"),
        prometheus_shape="classic histogram",
        identity_suffix="_count",
    ),
    MetricSpec(
        key="cumulative_exponential_histogram",
        field="exponential_histogram",
        temporality=CUMULATIVE,
        monotonic=False,
        reaggregation_supported=False,
        prometheus_suffixes=("_bucket", "_sum", "_count"),
        prometheus_shape="classic histogram",
        identity_suffix="_count",
    ),
    MetricSpec(
        key="summary",
        field="summary",
        temporality=metrics_pb2.AggregationTemporality.AGGREGATION_TEMPORALITY_UNSPECIFIED,
        monotonic=False,
        reaggregation_supported=False,
        prometheus_suffixes=("", "_sum", "_count"),
        prometheus_shape="summary",
        identity_suffix="_count",
    ),
)

SPECS_BY_KEY = {spec.key: spec for spec in METRIC_SPECS}


def key_values(attributes: Mapping[str, str]) -> list[common_pb2.KeyValue]:
    return [
        common_pb2.KeyValue(
            key=key,
            value=common_pb2.AnyValue(string_value=value),
        )
        for key, value in sorted(attributes.items())
    ]


def _number_point(
    attributes: Mapping[str, str],
    index: int,
    start: int,
    now: int,
) -> metrics_pb2.NumberDataPoint:
    return metrics_pb2.NumberDataPoint(
        attributes=key_values(attributes),
        start_time_unix_nano=start,
        time_unix_nano=now,
        as_double=float(index % 7 + 1),
    )


def _histogram_point(
    attributes: Mapping[str, str],
    index: int,
    start: int,
    now: int,
) -> metrics_pb2.HistogramDataPoint:
    counts = [0] * (len(EXPLICIT_BOUNDS) + 1)
    value = float(index % 7 + 1)
    counts[bisect_left(EXPLICIT_BOUNDS, value)] = 3
    return metrics_pb2.HistogramDataPoint(
        attributes=key_values(attributes),
        start_time_unix_nano=start,
        time_unix_nano=now,
        count=3,
        sum=3 * value,
        bucket_counts=counts,
        explicit_bounds=EXPLICIT_BOUNDS,
        min=value,
        max=value,
    )


def _exponential_point(
    attributes: Mapping[str, str],
    index: int,
    start: int,
    now: int,
) -> metrics_pb2.ExponentialHistogramDataPoint:
    counts = [0] * EXPONENTIAL_BUCKETS
    bucket_index = index % EXPONENTIAL_BUCKETS
    counts[bucket_index] = 3
    value = pow(2.0, (bucket_index + 0.5) / pow(2.0, EXPONENTIAL_SCALE))
    return metrics_pb2.ExponentialHistogramDataPoint(
        attributes=key_values(attributes),
        start_time_unix_nano=start,
        time_unix_nano=now,
        count=3,
        sum=3 * value,
        scale=EXPONENTIAL_SCALE,
        zero_count=0,
        zero_threshold=0.0,
        positive=metrics_pb2.ExponentialHistogramDataPoint.Buckets(
            offset=EXPONENTIAL_OFFSET,
            bucket_counts=counts,
        ),
        min=value,
        max=value,
    )


def _summary_point(
    attributes: Mapping[str, str],
    index: int,
    start: int,
    now: int,
) -> metrics_pb2.SummaryDataPoint:
    value = float(index % 7 + 1)
    return metrics_pb2.SummaryDataPoint(
        attributes=key_values(attributes),
        start_time_unix_nano=start,
        time_unix_nano=now,
        count=3,
        sum=3 * value,
        quantile_values=[
            metrics_pb2.SummaryDataPoint.ValueAtQuantile(
                quantile=quantile,
                value=value * (1 + quantile),
            )
            for quantile in QUANTILES
        ],
    )


def build_metric(
    spec: MetricSpec,
    attribute_sets: Sequence[Mapping[str, str]],
    start_unix_nano: int,
    time_unix_nano: int,
) -> metrics_pb2.Metric:
    metric = metrics_pb2.Metric(name=spec.metric_name, unit="")
    if spec.field == "gauge":
        metric.gauge.data_points.extend(
            _number_point(attributes, index, start_unix_nano, time_unix_nano)
            for index, attributes in enumerate(attribute_sets)
        )
    elif spec.field == "sum":
        metric.sum.aggregation_temporality = spec.temporality
        metric.sum.is_monotonic = spec.monotonic
        metric.sum.data_points.extend(
            _number_point(attributes, index, start_unix_nano, time_unix_nano)
            for index, attributes in enumerate(attribute_sets)
        )
    elif spec.field == "histogram":
        metric.histogram.aggregation_temporality = spec.temporality
        metric.histogram.data_points.extend(
            _histogram_point(attributes, index, start_unix_nano, time_unix_nano)
            for index, attributes in enumerate(attribute_sets)
        )
    elif spec.field == "exponential_histogram":
        metric.exponential_histogram.aggregation_temporality = spec.temporality
        metric.exponential_histogram.data_points.extend(
            _exponential_point(attributes, index, start_unix_nano, time_unix_nano)
            for index, attributes in enumerate(attribute_sets)
        )
    elif spec.field == "summary":
        metric.summary.data_points.extend(
            _summary_point(attributes, index, start_unix_nano, time_unix_nano)
            for index, attributes in enumerate(attribute_sets)
        )
    else:
        raise ValueError(f"unknown metric field: {spec.field}")
    return metric


def build_request(
    metrics: Sequence[metrics_pb2.Metric],
    resource_attributes: Mapping[str, str],
) -> metrics_service_pb2.ExportMetricsServiceRequest:
    return metrics_service_pb2.ExportMetricsServiceRequest(
        resource_metrics=[
            metrics_pb2.ResourceMetrics(
                resource=resource_pb2.Resource(
                    attributes=key_values(resource_attributes),
                ),
                scope_metrics=[
                    metrics_pb2.ScopeMetrics(
                        scope=common_pb2.InstrumentationScope(name=SCOPE_NAME),
                        metrics=list(metrics),
                    )
                ],
            )
        ]
    )


def datapoint_count(
    request: metrics_service_pb2.ExportMetricsServiceRequest,
) -> int:
    total = 0
    for resource_metrics in request.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                field = metric.WhichOneof("data")
                total += len(getattr(metric, field).data_points)
    return total


class Sender:
    """Blocking OTLP/gRPC metrics client over an insecure local channel."""

    def __init__(self, endpoint: str, timeout: float = 30.0) -> None:
        self._channel = grpc.insecure_channel(
            endpoint,
            options=[
                ("grpc.max_send_message_length", 64 * 1024 * 1024),
                ("grpc.max_receive_message_length", 16 * 1024 * 1024),
            ],
        )
        self._stub = metrics_service_pb2_grpc.MetricsServiceStub(self._channel)
        self._timeout = timeout

    def __enter__(self) -> Sender:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def send(
        self,
        request: metrics_service_pb2.ExportMetricsServiceRequest,
    ) -> int:
        response = self._stub.Export(request, timeout=self._timeout)
        return response.partial_success.rejected_data_points

    def close(self) -> None:
        self._channel.close()


def now_unix_nano() -> int:
    return time.time_ns()
