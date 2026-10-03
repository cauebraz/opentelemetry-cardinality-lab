import json
import statistics
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

Item = dict[str, Any] | None
Renderer = Callable[[Item], str]

LIMITATIONS = (
    "Single-host Docker Compose on one machine. Numbers are not a capacity "
    "model for a production Collector.",
    "Docker CPU and memory samples are diagnostic context only. The harness "
    "and the Collector share the same host, so they do not isolate processor "
    "overhead.",
    "Cardinality estimation upstream is HyperLogLog++ with about 0.81% "
    "standard error, so retained counts near the threshold vary between runs.",
    "The Collector's Prometheus exporter renders OTLP exponential histograms "
    "as classic bucketed histograms, so this report cannot say anything "
    "about native-histogram handling.",
    "Each run starts a fresh stack, so results exclude warm-cache and "
    "long-running drift effects.",
    "One threshold and one epoch duration per run; the report does not sweep them.",
)


def stats(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"median": None, "min": None, "max": None, "samples": []}
    return {
        "median": round(statistics.median(values), 6),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
        "samples": list(values),
    }


def cell(summary: dict[str, Any] | None, digits: int = 0) -> str:
    if summary is None or summary["median"] is None:
        return "not run"
    shape = f"{{:.{digits}f}}"
    if summary["min"] == summary["max"]:
        return shape.format(summary["median"])
    return (
        f"{shape.format(summary['median'])} "
        f"({shape.format(summary['min'])}-{shape.format(summary['max'])})"
    )


def percent(summary: dict[str, Any] | None) -> str:
    if summary is None or summary["median"] is None:
        return "not run"
    return f"{summary['median'] * 100:.1f}%"


def field(name: str, digits: int = 0) -> Renderer:
    def render(item: Item) -> str:
        return "not run" if item is None else cell(item[name], digits)

    return render


def reduction_percent(item: Item) -> str:
    return "not run" if item is None else percent(item["reduction"])


def summarize(payload: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for run in payload["runs"]:
        for metric_key, observation in run["metrics"].items():
            key = (run["suite"], run["case"], run["mode"], metric_key)
            grouped.setdefault(key, []).append({"run": run, "observation": observation})

    summary = []
    for (suite, case, mode, metric_key), entries in grouped.items():
        observations = [entry["observation"] for entry in entries]
        runs = [entry["run"] for entry in entries]
        first = observations[0]
        summary.append(
            {
                "suite": suite,
                "case": case,
                "mode": mode,
                "metric_key": metric_key,
                "identity_family": first["identity_family"],
                "reaggregation_supported": first["reaggregation_supported"],
                "prometheus_shape": first["prometheus_shape"],
                "unique_values": runs[0]["unique_values"],
                "input_identity_series": first["input_identity_series"],
                "repetitions": len(entries),
                "output_identity_series": stats(
                    [item["output_identity_series"] for item in observations]
                ),
                "reduction": stats([item["reduction"] for item in observations]),
                "total_family_series": stats(
                    [item["total_family_series"] for item in observations]
                ),
                "overflow_marker_series": stats(
                    [item["labels"]["overflow_marker_series"] for item in observations]
                ),
                "overflow_sentinel_series": stats(
                    [
                        item["labels"]["overflow_sentinel_series"]
                        for item in observations
                    ]
                ),
                "distinct_label_values": {
                    label: stats(
                        [
                            item["labels"]["distinct_label_values"][label]
                            for item in observations
                        ]
                    )
                    for label in first["labels"]["distinct_label_values"]
                },
                "send_seconds": stats([run["sending"]["send_seconds"] for run in runs]),
                "achieved_datapoints_per_second": stats(
                    [run["sending"]["achieved_datapoints_per_second"] for run in runs]
                ),
                "schedule_lag_seconds": stats(
                    [run["sending"]["schedule_lag_seconds"] for run in runs]
                ),
                "settle_seconds": stats([run["settle_seconds"] for run in runs]),
                "cpu_percent_samples": [
                    sample.get("CPUPerc")
                    for run in runs
                    for sample in run["resource_samples"]
                ],
                "memory_usage_samples": [
                    sample.get("MemUsage")
                    for run in runs
                    for sample in run["resource_samples"]
                ],
            }
        )
    summary.sort(key=lambda item: (item["suite"], item["case"], item["metric_key"]))
    return summary


def find(summary: list[dict[str, Any]], **filters: Any) -> list[dict[str, Any]]:
    return [
        item
        for item in summary
        if all(item.get(key) == value for key, value in filters.items())
    ]


def one(summary: list[dict[str, Any]], **filters: Any) -> Item:
    return next(iter(find(summary, **filters)), None)


def table(
    lines: list[str],
    header: str,
    modes: list[str],
    rows: list[tuple[str, list[Item]]],
    renderer: Renderer,
) -> None:
    lines.append(f"| {header} | " + " | ".join(modes) + " |")
    lines.append("| --- | " + " | ".join("---" for _ in modes) + " |")
    for label, items in rows:
        lines.append(f"| {label} | " + " | ".join(renderer(it) for it in items) + " |")
    lines.append("")


def by_mode(
    summary: list[dict[str, Any]],
    modes: list[str],
    **filters: Any,
) -> list[Item]:
    return [one(summary, mode=mode, **filters) for mode in modes]


def correctness_section(lines: list[str], directory: Path) -> None:
    lines.append("## Correctness")
    lines.append("")
    lines.append(
        "The correctness suite is separate from this benchmark and is not "
        "re-run here. It asserts the exact series, label and overflow-marker "
        "counts the processor must produce, and it fails the run on any "
        "mismatch."
    )
    lines.append("")
    source = directory.parent / "results.json"
    if not source.exists():
        lines.append("No correctness artifact was found at `results/results.json`.")
        lines.append("")
        return
    recorded = json.loads(source.read_text(encoding="utf-8"))
    failures = recorded.get("assertion_failures")
    lines.append("- Source: `results/REPORT.md`")
    lines.append(
        f"- Runs: {len(recorded['runs'])} across modes {', '.join(recorded['modes'])}"
    )
    lines.append(
        "- Assertion failures: " + ("none" if failures == [] else str(len(failures)))
    )
    lines.append("")


def metric_compatibility_section(
    lines: list[str],
    modes: list[str],
    summary: list[dict[str, Any]],
) -> None:
    lines.append("## Metric compatibility")
    lines.append("")
    rows = find(summary, suite="metrics")
    if not rows:
        lines.append("Not run.")
        lines.append("")
        return
    lines.append(
        "Measured fact: identity series present in Prometheus for each OTLP "
        f"metric shape, from {rows[0]['input_identity_series']} unique input "
        "attribute sets per shape."
    )
    lines.append("")
    keys = sorted({row["metric_key"] for row in rows})
    labelled = []
    for key in keys:
        sample = one(summary, suite="metrics", metric_key=key)
        notes = [sample["prometheus_shape"]]
        if sample["reaggregation_supported"]:
            notes.append("reaggregation supported")
        labelled.append(
            (
                f"{key} ({', '.join(notes)})",
                by_mode(summary, modes, suite="metrics", metric_key=key),
            )
        )
    table(lines, "metric shape", modes, labelled, field("output_identity_series"))
    lines.append("Complete Prometheus family series, including buckets and sums:")
    lines.append("")
    table(
        lines,
        "metric shape",
        modes,
        [
            (key, by_mode(summary, modes, suite="metrics", metric_key=key))
            for key in keys
        ],
        field("total_family_series"),
    )
    lines.append(
        "Interpretation: upstream reaggregates only Delta Sum and Gauge. "
        "Every other shape falls back to tagging, so its identity count under "
        "an enforcing mode stays at the baseline value."
    )
    lines.append("")


def epoch_section(
    lines: list[str],
    modes: list[str],
    payload: dict[str, Any],
    summary: list[dict[str, Any]],
) -> None:
    lines.append("## Epoch behavior")
    lines.append("")
    rows = find(summary, suite="epochs")
    if not rows:
        lines.append("Not run.")
        lines.append("")
        return
    cases = sorted({row["case"] for row in rows})
    lines.append(
        "Measured fact: identity series after the workload settles, for a "
        "burst inside one epoch and for sustained creation across epochs."
    )
    lines.append("")
    table(
        lines,
        "workload",
        modes,
        [
            (
                f"{case} ({one(summary, case=case)['unique_values']} unique)",
                by_mode(summary, modes, case=case),
            )
            for case in cases
        ],
        field("output_identity_series"),
    )
    multi_phase = [
        run
        for run in payload["runs"]
        if run["suite"] == "epochs" and len(run["sending"]["phases"]) > 1
    ]
    if multi_phase:
        names = [item["phase"] for item in multi_phase[0]["sending"]["phases"]]
        lines.append(
            "Identity series observed at each phase boundary of the sustained "
            "workload. These are sampled as the next phase starts, before the "
            "last scrape of the phase has settled, so the final column reads "
            "below the settled total above."
        )
        lines.append("")
        lines.append("| mode | " + " | ".join(names) + " |")
        lines.append("| --- | " + " | ".join("---" for _ in names) + " |")
        for mode in modes:
            matching = [run for run in multi_phase if run["mode"] == mode]
            if not matching:
                continue
            values = [
                cell(
                    stats(
                        [
                            next(
                                phase["output_identity_series"]
                                for phase in run["sending"]["phases"]
                                if phase["phase"] == name
                            )
                            for run in matching
                        ]
                    )
                )
                for name in names
            ]
            lines.append(f"| {mode} | " + " | ".join(values) + " |")
        lines.append("")
    lines.append(
        "Interpretation: enforcement compares each attribute key's current-epoch "
        "cardinality estimate with its previous-epoch estimate. The sustained "
        "workload replaces 200 identities with 200 different identities each "
        "epoch, so epochs two and three show no cardinality growth even though "
        "the identities churn completely. They pass through instead of receiving "
        "a fresh 20-value allowance."
    )
    lines.append("")


def scale_section(
    lines: list[str],
    modes: list[str],
    summary: list[dict[str, Any]],
) -> None:
    lines.append("## Scale")
    lines.append("")
    rows = find(summary, suite="scale")
    if not rows:
        lines.append("Not run.")
        lines.append("")
        return
    cases = sorted(
        {row["case"] for row in rows},
        key=lambda name: one(summary, case=name)["unique_values"],
    )
    built = [
        (
            str(one(summary, case=case)["unique_values"]),
            by_mode(summary, modes, case=case),
        )
        for case in cases
    ]
    lines.append(
        "Measured fact: identity series retained as unique attribute values "
        "grow, for a Delta Sum sent in one burst."
    )
    lines.append("")
    table(lines, "unique input values", modes, built, field("output_identity_series"))
    lines.append("Reduction against the unique input values:")
    lines.append("")
    table(lines, "unique input values", modes, built, reduction_percent)
    lines.append(
        "Interpretation: these bursts occur in the first epoch, when the previous "
        "estimate is zero. Enforcement therefore holds each tracked attribute "
        "near the configured growth threshold, plus distinct overflow identities "
        "created by bounded labels, while reduction rises with input cardinality."
    )
    lines.append("")


def offender_section(
    lines: list[str],
    modes: list[str],
    summary: list[dict[str, Any]],
) -> None:
    lines.append("## Multiple offenders")
    lines.append("")
    rows = find(summary, suite="offenders")
    if not rows:
        lines.append("Not run.")
        lines.append("")
        return
    case = rows[0]["case"]
    items = by_mode(summary, modes, case=case)
    lines.append(
        "Measured fact: one metric carrying four correlated unbounded "
        f"attributes, {rows[0]['unique_values']} unique combinations."
    )
    lines.append("")
    lines.append("| measurement | " + " | ".join(modes) + " |")
    lines.append("| --- | " + " | ".join("---" for _ in modes) + " |")
    for label, name in (
        ("identity series", "output_identity_series"),
        ("overflow-marked series", "overflow_marker_series"),
        ("overflow sentinel series", "overflow_sentinel_series"),
    ):
        values = [field(name)(item) for item in items]
        lines.append(f"| {label} | " + " | ".join(values) + " |")
    lines.append("")
    lines.append("Distinct values retained per unbounded attribute:")
    lines.append("")
    labels = sorted(rows[0]["distinct_label_values"])
    lines.append("| attribute | " + " | ".join(modes) + " |")
    lines.append("| --- | " + " | ".join("---" for _ in modes) + " |")
    for label in labels:
        values = [
            "not run" if item is None else cell(item["distinct_label_values"][label])
            for item in items
        ]
        lines.append(f"| {label} | " + " | ".join(values) + " |")
    lines.append("")
    lines.append(
        "Interpretation: the processor tracks each attribute key independently "
        "within a metric. Because the four unbounded attributes are perfectly "
        "correlated, their estimates cross the threshold at nearly the same "
        "point. The remaining bounded method and status labels then produce "
        "multiple reaggregated identities."
    )
    lines.append("")


def throughput_section(lines: list[str], summary: list[dict[str, Any]]) -> None:
    lines.append("## Workload execution")
    lines.append("")
    lines.append(
        "Measured fact: how fast the generator actually sent each case and "
        "how long Prometheus took to settle. These describe the harness, not "
        "the processor."
    )
    lines.append("")
    lines.append("| case | mode | datapoints/s | send s | settle s | lag s |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    seen = set()
    for item in summary:
        key = (item["case"], item["mode"])
        if key in seen:
            continue
        seen.add(key)
        lines.append(
            f"| {item['case']} | {item['mode']} | "
            f"{cell(item['achieved_datapoints_per_second'], 1)} | "
            f"{cell(item['send_seconds'], 2)} | "
            f"{cell(item['settle_seconds'], 2)} | "
            f"{cell(item['schedule_lag_seconds'], 3)} |"
        )
    lines.append("")


def limitations_section(lines: list[str], payload: dict[str, Any]) -> None:
    lines.append("## Limitations")
    lines.append("")
    lines.extend(f"- {text}" for text in LIMITATIONS)
    unavailable = sorted(
        {
            name
            for run in payload["runs"]
            for name in run["validity"].get("unavailable_counters", [])
        }
    )
    if unavailable:
        lines.append(
            "- Collector telemetry did not expose these counters: "
            f"`{'`, `'.join(unavailable)}`. `results.json` records them as "
            "`null`, not zero."
        )
    invalid = [run for run in payload["runs"] if not run["validity"]["ok"]]
    if invalid:
        lines.append(
            f"- {len(invalid)} run(s) failed a validity check; see `validity` "
            "in `results.json`."
        )
    lines.append("")


def render_markdown(payload: dict[str, Any], directory: Path) -> str:
    summary = payload["summary"]
    parameters = payload["parameters"]
    modes = list(parameters["modes"])
    lines = [
        "# Cardinality Guardian benchmark",
        "",
        f"- Platform: {payload['platform']}",
        "- Versions: "
        + ", ".join(f"{key} {value}" for key, value in payload["versions"].items()),
        f"- Modes: {', '.join(modes)}",
        f"- Repetitions per case: {parameters['repetitions']}",
        f"- Threshold: {parameters['threshold']} new values per "
        f"{parameters['epoch_seconds']}s epoch",
        f"- Suites: {', '.join(parameters['suites'])}",
        f"- Runs recorded: {len(payload['runs'])}",
        "",
        "Cells show the median across repetitions, with the min-max range in "
        "parentheses when repetitions disagreed.",
        "",
    ]
    correctness_section(lines, directory)
    metric_compatibility_section(lines, modes, summary)
    epoch_section(lines, modes, payload, summary)
    scale_section(lines, modes, summary)
    offender_section(lines, modes, summary)
    throughput_section(lines, summary)
    limitations_section(lines, payload)
    return "\n".join(lines).rstrip() + "\n"


def write_benchmark_results(payload: dict[str, Any], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    payload["summary"] = summarize(payload)
    (directory / "results.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (directory / "REPORT.md").write_text(
        render_markdown(payload, directory),
        encoding="utf-8",
    )
