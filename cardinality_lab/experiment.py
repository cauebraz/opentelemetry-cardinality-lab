import argparse
import platform
import time
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from cardinality_lab.generate import emit
from cardinality_lab.prometheus import (
    cardinality_self_metrics,
    query_result,
    query_scalar,
    wait_for_series,
)
from cardinality_lab.report import write_results
from cardinality_lab.scenarios import SCENARIOS, Scenario
from cardinality_lab.stack import (
    EPOCH_SECONDS,
    GUARDIAN_MODES,
    OTLP_ENDPOINT,
    PROMETHEUS_URL,
    RESULTS,
    THRESHOLD,
    collector_stats,
    environment_for,
    save_logs,
    start,
    stop,
)

MODES = GUARDIAN_MODES


def label_profile(scenario: Scenario) -> dict[str, Any]:
    results = query_result(PROMETHEUS_URL, scenario.prometheus_name)
    metrics = [result["metric"] for result in results]
    dynamic_attribute = scenario.prometheus_dynamic_attribute
    dynamic_values = (
        [metric[dynamic_attribute] for metric in metrics if dynamic_attribute in metric]
        if dynamic_attribute
        else []
    )
    return {
        "dynamic_attribute": dynamic_attribute,
        "dynamic_label_series": len(dynamic_values),
        "overflow_marker_series": sum(
            metric.get("otel_metric_overflow") == "true" for metric in metrics
        ),
        "overflow_values": sorted(
            {value for value in dynamic_values if "overflow" in value}
        ),
    }


def run_once(mode: str, repetition: int, points: int) -> dict[str, Any]:
    environment = environment_for(mode)
    start(environment)
    started = time.monotonic()
    series: dict[str, int] = {}
    labels: dict[str, dict[str, Any]] = {}
    emissions: list[dict[str, Any]] = []

    try:
        for scenario in SCENARIOS:
            emissions.append(asdict(emit(scenario, points, OTLP_ENDPOINT)))
            wait_for_series(PROMETHEUS_URL, scenario.prometheus_name)
            time.sleep(1.25)
            series[scenario.name] = round(
                query_scalar(
                    PROMETHEUS_URL,
                    f"count({scenario.prometheus_name})",
                )
            )
            labels[scenario.name] = label_profile(scenario)
        time.sleep(1.25)
        return {
            "mode": mode,
            "repetition": repetition,
            "series": series,
            "labels": labels,
            "emissions": emissions,
            "self_metrics": cardinality_self_metrics(PROMETHEUS_URL),
            "collector_stats": collector_stats(environment),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    finally:
        save_logs(RESULTS / f"{mode}-{repetition}-collector.log", environment)
        stop(environment)


def assertion_failures(payload: dict[str, Any]) -> list[str]:
    failures = []
    points = payload["parameters"]["points"]
    for run in payload["runs"]:
        for scenario in SCENARIOS:
            expected = scenario.expected_series(run["mode"], points, THRESHOLD)
            actual = run["series"][scenario.name]
            if actual != expected:
                failures.append(
                    f"{run['mode']} repetition {run['repetition']} "
                    f"{scenario.name}: expected {expected}, got {actual}"
                )
            profile = run["labels"][scenario.name]
            expected_dynamic = scenario.expected_dynamic_label_series(
                run["mode"],
                points,
                THRESHOLD,
            )
            if profile["dynamic_label_series"] != expected_dynamic:
                failures.append(
                    f"{run['mode']} repetition {run['repetition']} "
                    f"{scenario.name} dynamic labels: expected {expected_dynamic}, "
                    f"got {profile['dynamic_label_series']}"
                )
            expected_marked = scenario.expected_overflow_marker_series(
                run["mode"],
                points,
                THRESHOLD,
            )
            if profile["overflow_marker_series"] != expected_marked:
                failures.append(
                    f"{run['mode']} repetition {run['repetition']} "
                    f"{scenario.name} overflow markers: expected {expected_marked}, "
                    f"got {profile['overflow_marker_series']}"
                )
            expected_values = scenario.expected_overflow_values(run["mode"])
            if profile["overflow_values"] != expected_values:
                failures.append(
                    f"{run['mode']} repetition {run['repetition']} "
                    f"{scenario.name} overflow values: expected {expected_values}, "
                    f"got {profile['overflow_values']}"
                )
    return failures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--points", type=int, default=200)
    args = parser.parse_args()
    if args.repetitions < 1:
        raise ValueError("--repetitions must be at least 1")
    if args.points <= THRESHOLD:
        raise ValueError(f"--points must be greater than {THRESHOLD}")

    payload: dict[str, Any] = {
        "collected_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "versions": {
            "collector": "0.162.0",
            "prometheus": "3.15.0",
            "otel_python": "1.45.0",
            "python": platform.python_version(),
        },
        "parameters": {
            "points": args.points,
            "repetitions": args.repetitions,
            "threshold": THRESHOLD,
            "epoch_seconds": EPOCH_SECONDS,
        },
        "modes": list(MODES),
        "runs": [],
    }
    try:
        for mode in MODES:
            for repetition in range(1, args.repetitions + 1):
                print(f"running mode={mode} repetition={repetition}")
                payload["runs"].append(run_once(mode, repetition, args.points))
    finally:
        write_results(payload, RESULTS)

    failures = assertion_failures(payload)
    payload["assertion_failures"] = failures
    write_results(payload, RESULTS)
    if failures:
        raise RuntimeError("\n".join(failures))
    print(f"results: {RESULTS / 'REPORT.md'}")


if __name__ == "__main__":
    main()
