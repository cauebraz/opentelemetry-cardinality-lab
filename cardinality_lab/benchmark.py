import argparse
import json
import platform
import time
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cardinality_lab.benchmark_report import write_benchmark_results
from cardinality_lab.otlp import (
    DELTA,
    METRIC_SPECS,
    SPECS_BY_KEY,
    MetricSpec,
    Sender,
    build_metric,
    build_request,
    datapoint_count,
    now_unix_nano,
)
from cardinality_lab.prometheus import (
    cardinality_self_metrics,
    collector_counters,
    families_series_count,
    label_profile,
    query_value,
    series_count,
    series_count_by_job,
    wait_for_accepted_points,
    wait_until_stable,
)
from cardinality_lab.sdk_emitter import emit_histogram
from cardinality_lab.stack import (
    BENCHMARK_RESULTS,
    EPOCH_SECONDS,
    HISTOGRAM_RESULTS,
    MODES,
    OTLP_ENDPOINT,
    PROMETHEUS_URL,
    SDK_EMITTER,
    STRATEGIES_BY_NAME,
    THRESHOLD,
    Strategy,
    collector_stats,
    environment_for,
    log_summary,
    save_logs,
    start,
    stop,
    strategy_for,
)
from cardinality_lab.workloads import (
    WORKLOADS_BY_NAME,
    Batch,
    burst_plan,
    plan_duration_seconds,
    plan_unique_values,
    sustained_plan,
)

RESULTS_VERSION = 5
SERVICE_NAME = "otel-cardinality-lab"
MATRIX_UNIQUE_VALUES = 200
BURST_UNIQUE_VALUES = 2000
EPOCH_UNIQUE_VALUES = 200
EPOCH_REPEATS = 3
SCALE_VALUES = (100, 1_000, 10_000)
LARGE_SCALE_VALUES = (100_000,)
OFFENDER_UNIQUE_VALUES = 1_000
HISTOGRAM_UNIQUE_VALUES = 200
RESOURCE_SAMPLES = 3
DEFAULT_METRIC = "delta_sum"
HISTOGRAM_METRIC = "delta_histogram"
HISTOGRAM_SUITE = "histograms"
HISTOGRAM_STRATEGIES = (
    "baseline",
    "strip_and_reaggregate",
    "transform-delete",
    "transform-aggregate",
    "filter",
    "sdk-baseline",
    "sdk-view",
    "routing",
    "routing-string",
    "exponential",
    "exponential-native",
)
HISTOGRAM_FAMILY_SUFFIXES = ("_bucket", "_sum", "_count", "")
SAMPLED_USER = "user-000042"
SAMPLED_STATUS = "500"


@dataclass(frozen=True)
class Case:
    suite: str
    name: str
    description: str
    workload: str
    metric_keys: tuple[str, ...]
    unique_values: int
    plan: list[Batch] = field(repr=False)
    strategies: tuple[str, ...] = ()

    @property
    def specs(self) -> tuple[MetricSpec, ...]:
        return tuple(SPECS_BY_KEY[key] for key in self.metric_keys)

    def modes(self, requested: tuple[str, ...]) -> tuple[str, ...]:
        return self.strategies or requested


def matrix_cases() -> list[Case]:
    return [
        Case(
            suite="metrics",
            name="metric-compatibility",
            description=(
                "Every OTLP metric shape sent together so each type is "
                "observed under identical conditions."
            ),
            workload="single-offender",
            metric_keys=tuple(spec.key for spec in METRIC_SPECS),
            unique_values=MATRIX_UNIQUE_VALUES,
            plan=burst_plan(MATRIX_UNIQUE_VALUES),
        )
    ]


def epoch_cases(epoch_seconds: int) -> list[Case]:
    return [
        Case(
            suite="epochs",
            name="burst-single-epoch",
            description=(
                f"{BURST_UNIQUE_VALUES} new attribute values as fast as the "
                "sender allows, inside one epoch."
            ),
            workload="single-offender",
            metric_keys=(DEFAULT_METRIC,),
            unique_values=BURST_UNIQUE_VALUES,
            plan=burst_plan(BURST_UNIQUE_VALUES),
        ),
        Case(
            suite="epochs",
            name="sustained-across-epochs",
            description=(
                f"{EPOCH_UNIQUE_VALUES} new attribute values per epoch held "
                f"across {EPOCH_REPEATS} consecutive {epoch_seconds}s epochs."
            ),
            workload="single-offender",
            metric_keys=(DEFAULT_METRIC,),
            unique_values=EPOCH_UNIQUE_VALUES * EPOCH_REPEATS,
            plan=sustained_plan(
                EPOCH_UNIQUE_VALUES,
                EPOCH_REPEATS,
                epoch_seconds,
            ),
        ),
    ]


def scale_cases(values: tuple[int, ...]) -> list[Case]:
    return [
        Case(
            suite="scale",
            name=f"scale-{value}",
            description=f"{value} unique attribute values in one burst.",
            workload="single-offender",
            metric_keys=(DEFAULT_METRIC,),
            unique_values=value,
            plan=burst_plan(value),
        )
        for value in values
    ]


def offender_cases() -> list[Case]:
    return [
        Case(
            suite="offenders",
            name="multiple-offenders",
            description=(
                "Four correlated unbounded attributes on one metric, to show "
                "what the Guardian does when more than one label explodes."
            ),
            workload="multiple-offenders",
            metric_keys=(DEFAULT_METRIC,),
            unique_values=OFFENDER_UNIQUE_VALUES,
            plan=burst_plan(OFFENDER_UNIQUE_VALUES),
        )
    ]


@dataclass(frozen=True)
class Question:
    """One thing an operator asks of a histogram, in both representations."""

    name: str
    description: str
    classic: str
    native: str

    def expression(self, representation: str) -> str:
        return self.native if representation == "native" else self.classic


def questions_for(base: str) -> tuple[Question, ...]:
    return (
        Question(
            name="total_requests",
            description="How many observations the metric carries.",
            classic=f"sum({base}_count)",
            native=f"sum(histogram_count({base}))",
        ),
        Question(
            name="p95_by_method",
            description="The slowest 95th percentile across HTTP methods.",
            classic=(
                f"max(histogram_quantile(0.95, sum by (le, http_method) "
                f"({base}_bucket)))"
            ),
            native=(f"max(histogram_quantile(0.95, sum by (http_method) ({base})))"),
        ),
        Question(
            name="requests_by_status",
            description=f"Observations carrying http.status_code {SAMPLED_STATUS}.",
            classic=f'sum({base}_count{{http_status_code="{SAMPLED_STATUS}"}})',
            native=(
                f'sum(histogram_count({base}{{http_status_code="{SAMPLED_STATUS}"}}))'
            ),
        ),
        Question(
            name="p95_for_one_user",
            description=f"The 95th percentile for {SAMPLED_USER} alone.",
            classic=(
                f"histogram_quantile(0.95, sum by (le) "
                f'({base}_bucket{{user_id="{SAMPLED_USER}"}}))'
            ),
            native=(
                f'histogram_quantile(0.95, sum({base}{{user_id="{SAMPLED_USER}"}}))'
            ),
        ),
    )


def histogram_cases() -> list[Case]:
    return [
        Case(
            suite=HISTOGRAM_SUITE,
            name="histogram-reduction",
            description=(
                f"{HISTOGRAM_UNIQUE_VALUES} unique attribute sets on one delta "
                "histogram, under each strategy that claims to reduce "
                "histogram cardinality."
            ),
            workload="histogram-user-id",
            metric_keys=(HISTOGRAM_METRIC,),
            unique_values=HISTOGRAM_UNIQUE_VALUES,
            plan=burst_plan(HISTOGRAM_UNIQUE_VALUES),
            strategies=HISTOGRAM_STRATEGIES,
        )
    ]


DEFAULT_SUITES = ("metrics", "epochs", "scale", "offenders")
SUITES = (*DEFAULT_SUITES, HISTOGRAM_SUITE)


def build_cases(
    suites: tuple[str, ...],
    epoch_seconds: int,
    include_large_scale: bool,
) -> list[Case]:
    scale_values = SCALE_VALUES + (LARGE_SCALE_VALUES if include_large_scale else ())
    catalog = {
        "metrics": matrix_cases,
        "epochs": lambda: epoch_cases(epoch_seconds),
        "scale": lambda: scale_cases(scale_values),
        "offenders": offender_cases,
        HISTOGRAM_SUITE: histogram_cases,
    }
    cases: list[Case] = []
    for suite in suites:
        if suite not in catalog:
            raise ValueError(f"unknown suite: {suite}")
        cases.extend(catalog[suite]())
    return cases


def resource_samples(environment: dict[str, str]) -> list[dict[str, Any]]:
    samples = []
    for position in range(RESOURCE_SAMPLES):
        if position:
            time.sleep(1.0)
        samples.append(collector_stats(environment))
    return samples


def observe_metric(spec: MetricSpec, unique_values: int, labels: list[str]) -> dict:
    families = families_series_count(PROMETHEUS_URL, spec.prometheus_families())
    output = series_count(PROMETHEUS_URL, spec.identity_family)
    return {
        "identity_family": spec.identity_family,
        "input_identity_series": unique_values,
        "output_identity_series": output,
        "reduction": round(1 - output / unique_values, 6) if unique_values else 0.0,
        "family_series": families,
        "total_family_series": sum(families.values()),
        "reaggregation_supported": spec.reaggregation_supported,
        "prometheus_shape": spec.prometheus_shape,
        "labels": label_profile(PROMETHEUS_URL, spec.identity_family, labels),
    }


def histogram_observation(
    spec: MetricSpec,
    unique_values: int,
    strategy: Strategy,
    labels: list[str],
) -> dict[str, Any]:
    """What a histogram looks like in Prometheus, in either representation."""
    base = spec.prometheus_base
    families = {
        f"{base}{suffix}": series_count(PROMETHEUS_URL, f"{base}{suffix}")
        for suffix in HISTOGRAM_FAMILY_SUFFIXES
    }
    classic = families[f"{base}_count"]
    native = families[base]
    if classic:
        representation = "classic"
        identity_family = f"{base}_count"
    elif native:
        representation = "native"
        identity_family = base
    else:
        representation = "absent"
        identity_family = f"{base}_count"
    output = families[identity_family]
    return {
        "identity_family": identity_family,
        "representation": representation,
        "input_identity_series": unique_values,
        "output_identity_series": output,
        "reduction": round(1 - output / unique_values, 6) if unique_values else 0.0,
        "family_series": families,
        "total_family_series": sum(families.values()),
        "series_by_job": series_count_by_job(
            PROMETHEUS_URL,
            [f"{base}{suffix}" for suffix in HISTOGRAM_FAMILY_SUFFIXES],
            strategy.jobs,
        ),
        "questions": answer_questions(base, representation),
        "reaggregation_supported": spec.reaggregation_supported,
        "prometheus_shape": spec.prometheus_shape,
        "labels": label_profile(PROMETHEUS_URL, identity_family, labels),
    }


def answer_questions(base: str, representation: str) -> dict[str, Any]:
    answers: dict[str, Any] = {}
    for question in questions_for(base):
        expression = question.expression(representation)
        value: float | None = None
        error: str | None = None
        try:
            value = query_value(PROMETHEUS_URL, expression)
        except (OSError, urllib.error.HTTPError) as caught:
            error = str(caught)
        answers[question.name] = {
            "query": expression,
            "value": value,
            "error": error,
        }
    return answers


def send_sdk(
    case: Case,
    strategy: Strategy,
    resource_attributes: dict[str, str],
) -> dict[str, Any]:
    """The SDK aggregates before export, so the sent datapoints are unknown."""
    workload = WORKLOADS_BY_NAME[case.workload]
    spec = case.specs[0]
    emitted = emit_histogram(
        spec.metric_name,
        workload.attribute_sets(0, case.unique_values),
        resource_attributes,
        OTLP_ENDPOINT,
        strategy.sdk_aggregation or "explicit",
        strategy.sdk_attribute_keys,
    )
    send_seconds = float(emitted["send_seconds"])
    recorded = int(emitted["recorded_measurements"])
    return {
        "emitter": SDK_EMITTER,
        "sent_datapoints": None,
        "recorded_measurements": recorded,
        "exported_datapoints": None,
        "sent_batches": 1,
        "rejected_datapoints": 0,
        "send_seconds": round(send_seconds, 3),
        "schedule_lag_seconds": 0.0,
        "plan_duration_seconds": 0.0,
        "achieved_datapoints_per_second": 0.0,
        "achieved_unique_values_per_second": round(case.unique_values / send_seconds, 1)
        if send_seconds > 0
        else 0.0,
        "phases": [],
    }


def resolve_exported_rate(
    sending: dict[str, Any],
    counters: dict[str, float | None],
) -> None:
    """The SDK aggregates before export, so its rate comes from the receiver.

    Both emitters then report datapoints per second over the same unit, instead
    of recordings for one and exported datapoints for the other.
    """
    exported = sending["sent_datapoints"]
    if exported is None:
        accepted = counters["otelcol_receiver_accepted_metric_points"]
        exported = None if accepted is None else int(accepted)
    sending["exported_datapoints"] = exported
    seconds = sending["send_seconds"]
    if exported is not None and seconds > 0:
        sending["achieved_datapoints_per_second"] = round(exported / seconds, 1)


def send_plan(
    case: Case,
    sender: Sender,
    resource_attributes: dict[str, str],
) -> dict[str, Any]:
    workload = WORKLOADS_BY_NAME[case.workload]
    run_start_nano = now_unix_nano()
    previous_nano = run_start_nano
    started = time.monotonic()
    sent_datapoints = 0
    rejected = 0
    lag = 0.0
    phases: list[dict[str, Any]] = []
    seen_phase = None

    for batch in case.plan:
        target = started + batch.start_offset_seconds
        delay = target - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        lag = max(lag, time.monotonic() - target)
        if seen_phase is not None and batch.phase != seen_phase:
            phases.append(phase_observation(case, seen_phase, started))
        seen_phase = batch.phase

        now = now_unix_nano()
        attribute_sets = workload.attribute_sets(batch.first_index, batch.count)
        metrics = [
            build_metric(
                spec,
                attribute_sets,
                previous_nano if spec.temporality == DELTA else run_start_nano,
                now,
            )
            for spec in case.specs
        ]
        request = build_request(metrics, resource_attributes)
        rejected += sender.send(request)
        sent_datapoints += datapoint_count(request)
        previous_nano = now

    if seen_phase is not None:
        phases.append(phase_observation(case, seen_phase, started))

    send_seconds = time.monotonic() - started
    return {
        "emitter": "otlp",
        "sent_datapoints": sent_datapoints,
        "exported_datapoints": sent_datapoints,
        "sent_batches": len(case.plan),
        "rejected_datapoints": rejected,
        "send_seconds": round(send_seconds, 3),
        "schedule_lag_seconds": round(lag, 3),
        "plan_duration_seconds": plan_duration_seconds(case.plan),
        "achieved_datapoints_per_second": round(sent_datapoints / send_seconds, 1)
        if send_seconds > 0
        else 0.0,
        "achieved_unique_values_per_second": round(
            plan_unique_values(case.plan) / send_seconds, 1
        )
        if send_seconds > 0
        else 0.0,
        "phases": phases,
    }


def phase_observation(case: Case, phase: str, started: float) -> dict[str, Any]:
    spec = case.specs[0]
    return {
        "phase": phase,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "output_identity_series": series_count(PROMETHEUS_URL, spec.identity_family),
        "self_metrics": cardinality_self_metrics(PROMETHEUS_URL),
    }


def run_case(
    case: Case,
    mode: str,
    repetition: int,
    threshold: int,
    epoch_seconds: int,
    destination: Path = BENCHMARK_RESULTS,
) -> dict[str, Any]:
    workload = WORKLOADS_BY_NAME[case.workload]
    strategy = strategy_for(mode)
    histograms = case.suite == HISTOGRAM_SUITE
    environment = environment_for(mode, threshold, epoch_seconds)
    log_path = destination / "logs" / f"{case.name}-{mode}-{repetition}.log"
    log_text: str | None = None
    start(environment)
    started = time.monotonic()
    try:
        resource_attributes = {"service.name": SERVICE_NAME, "scenario": case.name}
        if strategy.emitter == SDK_EMITTER:
            sending = send_sdk(case, strategy, resource_attributes)
        else:
            with Sender(OTLP_ENDPOINT) as sender:
                sending = send_plan(case, sender, resource_attributes)
        settle_started = time.monotonic()
        if histograms:
            base = case.specs[0].prometheus_base
            families = [f"{base}{suffix}" for suffix in HISTOGRAM_FAMILY_SUFFIXES]
            wait_for_accepted_points(PROMETHEUS_URL, expected_points(case, sending))
            wait_until_stable(
                PROMETHEUS_URL,
                families,
                require_nonzero=strategy.exports,
            )
        else:
            families = [
                family for spec in case.specs for family in spec.prometheus_families()
            ]
            wait_until_stable(PROMETHEUS_URL, families)
        settle_seconds = time.monotonic() - settle_started
        labels = list(workload.unbounded_attributes)
        prometheus_labels = [label.replace(".", "_") for label in labels]
        if histograms:
            observations = {
                spec.key: histogram_observation(
                    spec,
                    case.unique_values,
                    strategy,
                    prometheus_labels,
                )
                for spec in case.specs
            }
        else:
            observations = {
                spec.key: observe_metric(spec, case.unique_values, prometheus_labels)
                for spec in case.specs
            }
        counters = collector_counters(PROMETHEUS_URL)
        resolve_exported_rate(sending, counters)
        log_text = save_logs(log_path, environment)
        return {
            "suite": case.suite,
            "case": case.name,
            "description": case.description,
            "mode": mode,
            "strategy": strategy.description,
            "repetition": repetition,
            "workload": case.workload,
            "unbounded_attributes": labels,
            "metric_keys": list(case.metric_keys),
            "unique_values": case.unique_values,
            "threshold": threshold,
            "epoch_seconds": epoch_seconds,
            "sending": sending,
            "settle_seconds": round(settle_seconds, 3),
            "total_seconds": round(time.monotonic() - started, 3),
            "metrics": observations,
            "collector_counters": counters,
            "collector_log": log_summary(log_text),
            "self_metrics": cardinality_self_metrics(PROMETHEUS_URL),
            "resource_samples": resource_samples(environment),
            "validity": validity(case, sending, counters, strategy.exports),
        }
    finally:
        if log_text is None:
            save_logs(log_path, environment)
        stop(environment)


def expected_points(case: Case, sending: dict[str, Any]) -> float:
    sent = sending["sent_datapoints"]
    return float(sent) if sent is not None else 1.0


def validity(
    case: Case,
    sending: dict[str, Any],
    counters: dict[str, float | None],
    expect_export: bool = True,
) -> dict[str, Any]:
    sent = sending["sent_datapoints"]
    notes = []
    if sent is not None:
        expected = plan_unique_values(case.plan) * len(case.metric_keys)
        if sent != expected:
            notes.append(f"sent {sent} datapoints, expected {expected}")
    if sending["rejected_datapoints"]:
        notes.append(f"{sending['rejected_datapoints']} datapoints rejected by OTLP")
    accepted = counters["otelcol_receiver_accepted_metric_points"]
    refused = counters["otelcol_receiver_refused_metric_points"]
    exported = counters["otelcol_exporter_sent_metric_points"]
    failed = counters["otelcol_exporter_send_failed_metric_points"]
    if accepted is None:
        notes.append("receiver accepted counter is unavailable")
    elif sent is not None and accepted != sent:
        notes.append(f"receiver accepted {accepted:g} datapoints, sent {sent}")
    elif sent is None and accepted <= 0:
        notes.append("receiver accepted no datapoints")
    if refused is None:
        notes.append("receiver refused counter is unavailable")
    elif refused:
        notes.append(f"receiver refused {refused:g} datapoints")
    if expect_export:
        if exported is None:
            notes.append("exporter sent counter is unavailable")
        elif accepted is not None and not 0 < exported <= accepted:
            notes.append(
                f"exporter sent {exported:g} datapoints after accepting {accepted:g}"
            )
    if failed:
        notes.append(f"exporter failed to send {failed:g} datapoints")
    planned = sending["plan_duration_seconds"]
    if planned and sending["schedule_lag_seconds"] > 1.0:
        notes.append(
            f"sender ran {sending['schedule_lag_seconds']}s behind the schedule"
        )
    unavailable = sorted(name for name, value in counters.items() if value is None)
    return {
        "ok": not notes,
        "notes": notes,
        "unavailable_counters": unavailable,
    }


RESUME_KEYS = (
    "suites",
    "modes",
    "threshold",
    "epoch_seconds",
    "repetitions",
    "large_scale",
)


def resume_from(
    source: Path,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], set[tuple[str, str, int]]]:
    """Reuse runs already recorded, so an interrupted benchmark can finish."""
    if not source.exists():
        return payload, set()
    previous = json.loads(source.read_text(encoding="utf-8"))
    if previous.get("results_version") != RESULTS_VERSION:
        raise ValueError(
            f"{source} has results_version "
            f"{previous.get('results_version')}, expected {RESULTS_VERSION}"
        )
    mismatched = [
        key
        for key in RESUME_KEYS
        if previous["parameters"].get(key) != payload["parameters"][key]
    ]
    if mismatched:
        raise ValueError(
            f"{source} was collected with different {', '.join(mismatched)}; "
            "resume requires identical parameters"
        )
    if previous.get("versions") != payload.get("versions"):
        raise ValueError(f"{source} was collected with different component versions")
    if previous.get("platform") != payload.get("platform"):
        raise ValueError(f"{source} was collected on a different platform")
    if previous.get("cases") != payload.get("cases"):
        raise ValueError(f"{source} contains different benchmark case definitions")
    payload["runs"] = list(previous["runs"])
    done = {(run["case"], run["mode"], run["repetition"]) for run in payload["runs"]}
    if len(done) != len(payload["runs"]):
        raise ValueError(f"{source} contains duplicate run keys")
    expected = {
        (case["name"], mode, repetition)
        for case in payload["cases"]
        for mode in (case["strategies"] or payload["parameters"]["modes"])
        for repetition in range(1, payload["parameters"]["repetitions"] + 1)
    }
    unexpected = done - expected
    if unexpected:
        raise ValueError(
            f"{source} contains runs outside the requested benchmark scope"
        )
    print(f"resuming from {source}: {len(done)} run(s) already recorded")
    return payload, done


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suites", default=",".join(DEFAULT_SUITES))
    parser.add_argument("--modes", default=None)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--threshold", type=int, default=THRESHOLD)
    parser.add_argument("--epoch-seconds", type=int, default=EPOCH_SECONDS)
    parser.add_argument("--large-scale", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    if args.repetitions < 1:
        raise ValueError("--repetitions must be at least 1")
    if args.epoch_seconds < 10:
        raise ValueError("--epoch-seconds must be at least 10")
    suites = tuple(item.strip() for item in args.suites.split(",") if item.strip())
    histograms_only = set(suites) == {HISTOGRAM_SUITE}
    if HISTOGRAM_SUITE in suites and not histograms_only:
        raise ValueError(
            f"the {HISTOGRAM_SUITE} suite writes its own report; run it alone"
        )
    destination = HISTOGRAM_RESULTS if histograms_only else BENCHMARK_RESULTS
    default_modes = HISTOGRAM_STRATEGIES if histograms_only else MODES
    requested = args.modes or ",".join(default_modes)
    modes = tuple(item.strip() for item in requested.split(",") if item.strip())
    for mode in modes:
        if mode not in STRATEGIES_BY_NAME:
            raise ValueError(f"unknown mode: {mode}")
    cases = build_cases(suites, args.epoch_seconds, args.large_scale)

    payload: dict[str, Any] = {
        "results_version": RESULTS_VERSION,
        "platform": platform.platform(),
        "versions": {
            "collector": "0.162.0",
            "prometheus": "3.15.0",
            "otel_proto": "1.45.0",
            "python": platform.python_version(),
        },
        "parameters": {
            "suites": list(suites),
            "modes": list(modes),
            "repetitions": args.repetitions,
            "threshold": args.threshold,
            "epoch_seconds": args.epoch_seconds,
            "large_scale": args.large_scale,
        },
        "cases": [
            {
                "suite": case.suite,
                "name": case.name,
                "description": case.description,
                "workload": case.workload,
                "metric_keys": list(case.metric_keys),
                "unique_values": case.unique_values,
                "batches": len(case.plan),
                "plan_duration_seconds": plan_duration_seconds(case.plan),
                "strategies": list(case.strategies),
            }
            for case in cases
        ],
        "runs": [],
    }
    done: set[tuple[str, str, int]] = set()
    if args.resume:
        payload, done = resume_from(destination / "results.json", payload)

    total = sum(len(case.modes(modes)) for case in cases) * args.repetitions
    position = 0
    try:
        for case in cases:
            for mode in case.modes(modes):
                for repetition in range(1, args.repetitions + 1):
                    position += 1
                    if (case.name, mode, repetition) in done:
                        continue
                    print(
                        f"[{position}/{total}] case={case.name} mode={mode} "
                        f"repetition={repetition}"
                    )
                    payload["runs"].append(
                        run_case(
                            case,
                            mode,
                            repetition,
                            args.threshold,
                            args.epoch_seconds,
                            destination,
                        )
                    )
    finally:
        write_benchmark_results(payload, destination)

    invalid = [run for run in payload["runs"] if not run["validity"]["ok"]]
    if invalid:
        raise RuntimeError(
            "\n".join(
                f"{run['case']} {run['mode']} repetition {run['repetition']}: "
                + "; ".join(run["validity"]["notes"])
                for run in invalid
            )
        )
    print(f"results: {destination / 'REPORT.md'}")


if __name__ == "__main__":
    main()
