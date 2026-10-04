import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cardinality_lab.prometheus import wait_for_url

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
BENCHMARK_RESULTS = RESULTS / "benchmark"
HISTOGRAM_RESULTS = RESULTS / "histograms"

PROMETHEUS_URL = "http://127.0.0.1:19090"
COLLECTOR_HEALTH_URL = "http://127.0.0.1:13133"
OTLP_ENDPOINT = "localhost:14317"

BASELINE_MODE = "baseline"
GUARDIAN_MODES = ("tag_only", "overflow_attribute", "strip_and_reaggregate")
MODES = (BASELINE_MODE, *GUARDIAN_MODES)

THRESHOLD = 20
EPOCH_SECONDS = 10

GUARDIAN_CONFIG = "./config/collector.yaml"
BASELINE_CONFIG = "./config/collector-baseline.yaml"
TRANSFORM_DELETE_CONFIG = "./config/collector-transform-delete.yaml"
TRANSFORM_AGGREGATE_CONFIG = "./config/collector-transform-aggregate.yaml"
FILTER_CONFIG = "./config/collector-filter.yaml"
ROUTING_CONFIG = "./config/collector-routing.yaml"
ROUTING_STRING_CONFIG = "./config/collector-routing-string.yaml"

PROMETHEUS_CONFIG = "./config/prometheus.yaml"
PROMETHEUS_ROUTING_CONFIG = "./config/prometheus-routing.yaml"
PROMETHEUS_NATIVE_CONFIG = "./config/prometheus-native.yaml"

MAIN_JOB = "lab-metrics"
OVERFLOW_JOB = "lab-overflow"

OTLP_EMITTER = "otlp"
SDK_EMITTER = "sdk"

KEPT_ATTRIBUTE_KEYS = (
    "environment",
    "region",
    "http.method",
    "http.status_code",
)


@dataclass(frozen=True)
class Strategy:
    """One way of running the stack: a Collector, a Prometheus and an emitter."""

    name: str
    description: str
    collector_config: str
    enforcement_mode: str = GUARDIAN_MODES[0]
    prometheus_config: str = PROMETHEUS_CONFIG
    emitter: str = OTLP_EMITTER
    sdk_aggregation: str | None = None
    sdk_attribute_keys: tuple[str, ...] | None = None
    jobs: tuple[str, ...] = (MAIN_JOB,)
    exports: bool = True


STRATEGIES: tuple[Strategy, ...] = (
    Strategy(
        name=BASELINE_MODE,
        description="The pipeline without the processor.",
        collector_config=BASELINE_CONFIG,
    ),
    *(
        Strategy(
            name=mode,
            description=f"Cardinality Guardian in {mode}.",
            collector_config=GUARDIAN_CONFIG,
            enforcement_mode=mode,
        )
        for mode in GUARDIAN_MODES
    ),
    Strategy(
        name="transform-delete",
        description="transform processor deleting the unbounded attribute key.",
        collector_config=TRANSFORM_DELETE_CONFIG,
    ),
    Strategy(
        name="transform-aggregate",
        description="transform processor aggregating on the remaining attributes.",
        collector_config=TRANSFORM_AGGREGATE_CONFIG,
    ),
    Strategy(
        name="filter",
        description="filter processor dropping every datapoint with the attribute.",
        collector_config=FILTER_CONFIG,
        exports=False,
    ),
    Strategy(
        name="sdk-baseline",
        description="Python SDK emitting every attribute, no processor.",
        collector_config=BASELINE_CONFIG,
        emitter=SDK_EMITTER,
        sdk_aggregation="explicit",
    ),
    Strategy(
        name="sdk-view",
        description="Python SDK view limiting the instrument to bounded attributes.",
        collector_config=BASELINE_CONFIG,
        emitter=SDK_EMITTER,
        sdk_aggregation="explicit",
        sdk_attribute_keys=KEPT_ATTRIBUTE_KEYS,
    ),
    Strategy(
        name="routing",
        description="Cardinality Guardian in tag_only routed to a second exporter.",
        collector_config=ROUTING_CONFIG,
        prometheus_config=PROMETHEUS_ROUTING_CONFIG,
        jobs=(MAIN_JOB, OVERFLOW_JOB),
    ),
    Strategy(
        name="routing-string",
        description="The same routing table, with the marker read as a string.",
        collector_config=ROUTING_STRING_CONFIG,
        prometheus_config=PROMETHEUS_ROUTING_CONFIG,
        jobs=(MAIN_JOB, OVERFLOW_JOB),
    ),
    Strategy(
        name="exponential",
        description="Python SDK exponential buckets, scraped as text.",
        collector_config=BASELINE_CONFIG,
        emitter=SDK_EMITTER,
        sdk_aggregation="exponential",
    ),
    Strategy(
        name="exponential-native",
        description="Python SDK exponential buckets, scraped as native histograms.",
        collector_config=BASELINE_CONFIG,
        prometheus_config=PROMETHEUS_NATIVE_CONFIG,
        emitter=SDK_EMITTER,
        sdk_aggregation="exponential",
    ),
)

STRATEGIES_BY_NAME = {strategy.name: strategy for strategy in STRATEGIES}


def command(
    *args: str,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        check=check,
        text=True,
        capture_output=True,
    )


def compose(
    *args: str,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return command("docker", "compose", *args, env=env, check=check)


def strategy_for(name: str) -> Strategy:
    if name not in STRATEGIES_BY_NAME:
        raise ValueError(f"unknown mode: {name}")
    return STRATEGIES_BY_NAME[name]


def environment_for(
    mode: str,
    threshold: int = THRESHOLD,
    epoch_seconds: int = EPOCH_SECONDS,
) -> dict[str, str]:
    strategy = strategy_for(mode)
    return os.environ | {
        "COLLECTOR_CONFIG": strategy.collector_config,
        "PROMETHEUS_CONFIG": strategy.prometheus_config,
        "ENFORCEMENT_MODE": strategy.enforcement_mode,
        "CARDINALITY_THRESHOLD": str(threshold),
        "EPOCH_SECONDS": str(epoch_seconds),
    }


def start(environment: dict[str, str]) -> None:
    compose("down", "--volumes", "--remove-orphans", env=environment, check=False)
    compose("up", "--detach", env=environment)
    wait_for_url(COLLECTOR_HEALTH_URL)
    wait_for_url(f"{PROMETHEUS_URL}/-/ready")


def stop(environment: dict[str, str] | None = None) -> None:
    compose("down", "--volumes", "--remove-orphans", env=environment, check=False)


STATS_FIELDS = ("CPUPerc", "MemPerc", "MemUsage")
LOG_TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\b[\t ]*")


def collector_stats(environment: dict[str, str] | None = None) -> dict[str, Any]:
    container_id = compose("ps", "--quiet", "collector", env=environment).stdout.strip()
    output = command(
        "docker",
        "stats",
        "--no-stream",
        "--format",
        "{{json .}}",
        container_id,
    ).stdout
    sample = json.loads(output)
    return {key: sample[key] for key in STATS_FIELDS if key in sample}


LOG_LEVEL = re.compile(r"\|\s+(debug|info|warn|error|dpanic|panic|fatal)\t")
DROPPED_HISTOGRAM_DATAPOINT = "Dropped misaligned histogram datapoint"


def collector_logs(environment: dict[str, str] | None = None) -> str:
    result = compose("logs", "--no-color", "collector", env=environment, check=False)
    return LOG_TIMESTAMP.sub("", result.stdout)


def log_summary(text: str) -> dict[str, int]:
    levels = [match.group(1) for match in LOG_LEVEL.finditer(text)]
    return {
        "warn_lines": levels.count("warn"),
        "error_lines": sum(levels.count(level) for level in ("error", "fatal")),
        "dropped_histogram_datapoints_logged": text.count(DROPPED_HISTOGRAM_DATAPOINT),
    }


def save_logs(destination: Path, environment: dict[str, str] | None = None) -> str:
    text = collector_logs(environment)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    return text
