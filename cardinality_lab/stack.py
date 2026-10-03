import json
import os
import subprocess
from pathlib import Path
from typing import Any

from cardinality_lab.prometheus import wait_for_url

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
BENCHMARK_RESULTS = RESULTS / "benchmark"

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


def environment_for(
    mode: str,
    threshold: int = THRESHOLD,
    epoch_seconds: int = EPOCH_SECONDS,
) -> dict[str, str]:
    if mode == BASELINE_MODE:
        config = BASELINE_CONFIG
        enforcement = GUARDIAN_MODES[0]
    elif mode in GUARDIAN_MODES:
        config = GUARDIAN_CONFIG
        enforcement = mode
    else:
        raise ValueError(f"unknown mode: {mode}")
    return os.environ | {
        "COLLECTOR_CONFIG": config,
        "ENFORCEMENT_MODE": enforcement,
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


def save_logs(destination: Path, environment: dict[str, str] | None = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = compose("logs", "--no-color", "collector", env=environment, check=False)
    destination.write_text(result.stdout, encoding="utf-8")
