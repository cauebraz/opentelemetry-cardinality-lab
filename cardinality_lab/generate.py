import argparse
import json
import os
import time
from dataclasses import asdict, dataclass

from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from cardinality_lab.scenarios import SCENARIOS_BY_NAME, Scenario


@dataclass(frozen=True)
class Emission:
    scenario: str
    points: int
    endpoint: str
    elapsed_seconds: float


def emit(scenario: Scenario, points: int, endpoint: str) -> Emission:
    os.environ["OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE"] = "DELTA"
    exporter = OTLPMetricExporter(endpoint=endpoint, insecure=True, timeout=10)
    reader = PeriodicExportingMetricReader(
        exporter,
        export_interval_millis=60_000,
        export_timeout_millis=10_000,
    )
    provider = MeterProvider(
        metric_readers=[reader],
        resource=Resource.create(
            {
                "service.name": "otel-cardinality-lab",
                "scenario": scenario.name,
            }
        ),
    )
    meter = provider.get_meter("cardinality-lab", "0.1.0")
    started = time.monotonic()

    if scenario.kind == "counter":
        instrument = meter.create_counter(scenario.metric_name)
        for index in range(points):
            instrument.add(1, scenario.attributes(index))
    else:
        instrument = meter.create_histogram(scenario.metric_name, unit="ms")
        for index in range(points):
            instrument.record(float((index % 100) + 1), scenario.attributes(index))

    if not provider.force_flush(timeout_millis=10_000):
        raise RuntimeError(f"metric export timed out for {scenario.name}")
    provider.shutdown()
    return Emission(
        scenario=scenario.name,
        points=points,
        endpoint=endpoint,
        elapsed_seconds=round(time.monotonic() - started, 6),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=sorted(SCENARIOS_BY_NAME))
    parser.add_argument("--points", type=int, default=200)
    parser.add_argument("--endpoint", default="localhost:14317")
    args = parser.parse_args()
    result = emit(SCENARIOS_BY_NAME[args.scenario], args.points, args.endpoint)
    print(json.dumps(asdict(result), sort_keys=True))


if __name__ == "__main__":
    main()
