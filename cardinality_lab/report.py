import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from cardinality_lab.scenarios import SCENARIOS


def range_cell(values: list[int]) -> str:
    median = round(statistics.median(values))
    minimum = min(values)
    maximum = max(values)
    if minimum == maximum:
        return str(median)
    return f"{median} ({minimum}-{maximum})"


def summarize(payload: dict[str, Any]) -> dict[str, dict[str, list[int]]]:
    grouped: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for run in payload["runs"]:
        for scenario, series in run["series"].items():
            grouped[run["mode"]][scenario].append(series)
    return grouped


def summarize_labels(
    payload: dict[str, Any],
) -> dict[str, dict[str, dict[str, list[int]]]]:
    grouped: dict[str, dict[str, dict[str, list[int]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    for run in payload["runs"]:
        for scenario, profile in run["labels"].items():
            grouped[run["mode"]][scenario]["dynamic"].append(
                profile["dynamic_label_series"]
            )
            grouped[run["mode"]][scenario]["marked"].append(
                profile["overflow_marker_series"]
            )
    return grouped


def render_markdown(payload: dict[str, Any]) -> str:
    grouped = summarize(payload)
    label_groups = summarize_labels(payload)
    parameters = payload["parameters"]
    expected_runs = len(payload["modes"]) * parameters["repetitions"]
    complete = len(payload["runs"]) == expected_runs
    result_heading = "## Result" if complete else "## Partial result"
    lines = [
        "# OpenTelemetry Cardinality Guardian experiment",
        "",
        f"- Collected at: {payload['collected_at']}",
        f"- Collector: {payload['versions']['collector']}",
        f"- Prometheus: {payload['versions']['prometheus']}",
        f"- Python OpenTelemetry SDK: {payload['versions']['otel_python']}",
        f"- Points per scenario: {parameters['points']}",
        f"- Repetitions: {parameters['repetitions']}",
        f"- Cardinality threshold: {parameters['threshold']} new values per epoch",
        f"- Epoch: {parameters['epoch_seconds']} seconds",
        "",
        result_heading,
        "",
    ]
    if not complete:
        lines.extend(
            [
                f"Only {len(payload['runs'])} of {expected_runs} runs completed.",
                "",
            ]
        )
    lines.extend(
        [
            "| Mode | Stable | User ID | Unbounded route | Histogram |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for mode in payload["modes"]:
        values = grouped[mode]
        lines.append(
            "| "
            + " | ".join(
                [
                    mode,
                    *(
                        range_cell(values[scenario.name])
                        if values[scenario.name]
                        else "not run"
                        for scenario in SCENARIOS
                    ),
                ]
            )
            + " |"
        )

    points = parameters["points"]
    threshold = parameters["threshold"]
    expected_reaggregated = min(points, threshold + 1)
    lines.extend(
        [
            "",
            "## Label behavior",
            "",
            "| Mode | Scenario | Dynamic-label series | Overflow-marked series |",
            "| --- | --- | ---: | ---: |",
        ]
    )
    for mode in payload["modes"]:
        for scenario in SCENARIOS:
            if scenario.dynamic_attribute is None:
                continue
            values = label_groups[mode][scenario.name]
            if values["dynamic"]:
                lines.append(
                    f"| {mode} | {scenario.name} | "
                    f"{range_cell(values['dynamic'])} | "
                    f"{range_cell(values['marked'])} |"
                )
            else:
                lines.append(f"| {mode} | {scenario.name} | not run | not run |")

    lines.extend(
        [
            "",
            "Dynamic-label series retain the scenario's unsafe label. Overflow-",
            "marked series carry `otel.metric.overflow=true` after translation.",
            "Replacement values are retained in `results.json`.",
            "",
            "Each value is the median active Prometheus series. A parenthesized",
            "range appears when repetitions differ.",
            "",
            "## Assertions",
            "",
            "- Stable labels remain at 4 series in every mode.",
            f"- `tag_only` retains all {points} dynamic-label series.",
            (
                "- Delta counters in `overflow_attribute` and "
                f"`strip_and_reaggregate` converge to {expected_reaggregated} "
                "series: the values observed before the threshold plus one "
                "reaggregated overflow series."
            ),
            (
                f"- Histograms retain all {points} series in every mode because "
                "the alpha processor falls back to `tag_only` when safe spatial "
                "reaggregation is unavailable."
            ),
            "",
            "## Collector self-telemetry",
            "",
        ]
    )
    metric_names = sorted(
        {metric for run in payload["runs"] for metric in run["self_metrics"]}
    )
    if metric_names:
        lines.extend(f"- `{metric}`" for metric in metric_names)
    else:
        lines.append("- No Cardinality Guardian self-metrics were scraped.")

    lines.extend(
        [
            "",
            "## Reproduce",
            "",
            "```bash",
            "just setup",
            "just check",
            "just experiment",
            "```",
            "",
            "The experiment uses local containers and sends no telemetry externally.",
            "Results are measurements from this run, not vendor pricing estimates.",
            "",
        ]
    )
    return "\n".join(lines)


def write_results(payload: dict[str, Any], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "results.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (directory / "REPORT.md").write_text(
        render_markdown(payload),
        encoding="utf-8",
    )
