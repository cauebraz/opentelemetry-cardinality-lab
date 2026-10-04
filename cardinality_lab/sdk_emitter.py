import os
import time
from collections.abc import Mapping, Sequence

from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import (
    ExplicitBucketHistogramAggregation,
    ExponentialBucketHistogramAggregation,
    View,
)
from opentelemetry.sdk.resources import Resource

from cardinality_lab.otlp import EXPLICIT_BOUNDS

SCOPE_NAME = "cardinality_lab.sdk"
SCOPE_VERSION = "0.1.0"
OBSERVATIONS_PER_IDENTITY = 3
AGGREGATIONS = ("explicit", "exponential")


def aggregation_for(name: str):
    if name == "explicit":
        return ExplicitBucketHistogramAggregation(boundaries=list(EXPLICIT_BOUNDS))
    if name == "exponential":
        return ExponentialBucketHistogramAggregation()
    raise ValueError(f"unknown SDK aggregation: {name}")


def view_for(
    metric_name: str,
    aggregation: str,
    attribute_keys: Sequence[str] | None,
) -> View:
    return View(
        instrument_name=metric_name,
        aggregation=aggregation_for(aggregation),
        attribute_keys=None if attribute_keys is None else set(attribute_keys),
    )


def observed_value(index: int) -> float:
    """The same value the OTLP encoder places in its histogram points."""
    return float(index % 7 + 1)


def emit_histogram(
    metric_name: str,
    attribute_sets: Sequence[Mapping[str, str]],
    resource_attributes: Mapping[str, str],
    endpoint: str,
    aggregation: str,
    attribute_keys: Sequence[str] | None = None,
) -> dict[str, float | int]:
    """Record one histogram per attribute set through the SDK and flush once."""
    os.environ["OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE"] = "DELTA"
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=endpoint, insecure=True, timeout=30),
        export_interval_millis=600_000,
        export_timeout_millis=30_000,
    )
    provider = MeterProvider(
        metric_readers=[reader],
        resource=Resource.create(dict(resource_attributes)),
        views=[view_for(metric_name, aggregation, attribute_keys)],
    )
    meter = provider.get_meter(SCOPE_NAME, SCOPE_VERSION)
    instrument = meter.create_histogram(metric_name)
    started = time.monotonic()
    recorded = 0
    for index, attributes in enumerate(attribute_sets):
        value = observed_value(index)
        for _ in range(OBSERVATIONS_PER_IDENTITY):
            instrument.record(value, dict(attributes))
            recorded += 1
    if not provider.force_flush(timeout_millis=30_000):
        raise RuntimeError(f"metric export timed out for {metric_name}")
    provider.shutdown()
    return {
        "recorded_measurements": recorded,
        "send_seconds": round(time.monotonic() - started, 3),
    }
