from pathlib import Path
from typing import Any

import pytest

from cardinality_lab.benchmark_report import (
    cell,
    find,
    percent,
    render_markdown,
    stats,
    summarize,
)

MODES = ["baseline", "tag_only", "strip_and_reaggregate"]
OUTPUT = {"baseline": 200, "tag_only": 200, "strip_and_reaggregate": 21}


def observation(mode: str, unique: int) -> dict[str, Any]:
    output = OUTPUT.get(mode, unique)
    return {
        "identity_family": "lab_bench_delta_sum_total",
        "input_identity_series": unique,
        "output_identity_series": output,
        "reduction": round(1 - output / unique, 6),
        "family_series": {"lab_bench_delta_sum_total": output},
        "total_family_series": output,
        "reaggregation_supported": True,
        "prometheus_shape": "counter",
        "labels": {
            "series": output,
            "distinct_label_values": {"user_id": output},
            "overflow_marker_series": 180 if mode == "tag_only" else 0,
            "overflow_sentinel_series": 0,
        },
    }


def run(suite: str, case: str, mode: str, repetition: int, unique: int) -> dict:
    return {
        "suite": suite,
        "case": case,
        "description": "synthetic",
        "mode": mode,
        "repetition": repetition,
        "workload": "single-offender",
        "unbounded_attributes": ["user.id"],
        "metric_keys": ["delta_sum"],
        "unique_values": unique,
        "threshold": 20,
        "epoch_seconds": 10,
        "sending": {
            "sent_datapoints": unique,
            "sent_batches": 1,
            "rejected_datapoints": 0,
            "send_seconds": 0.5 + repetition / 100,
            "schedule_lag_seconds": 0.0,
            "plan_duration_seconds": 0.0,
            "achieved_datapoints_per_second": 400.0,
            "achieved_unique_values_per_second": 400.0,
            "phases": [
                {
                    "phase": f"epoch-{index}",
                    "elapsed_seconds": index * 10.0,
                    "output_identity_series": OUTPUT[mode] * index,
                    "self_metrics": {},
                }
                for index in (1, 2, 3)
            ]
            if case == "sustained-across-epochs"
            else [],
        },
        "settle_seconds": 3.0,
        "total_seconds": 20.0,
        "metrics": {"delta_sum": observation(mode, unique)},
        "collector_counters": {"otelcol_receiver_accepted_metric_points": unique},
        "self_metrics": {},
        "resource_samples": [{"CPUPerc": "1.50%", "MemUsage": "40MiB / 8GiB"}],
        "validity": {"ok": True, "notes": []},
    }


@pytest.fixture
def payload() -> dict[str, Any]:
    runs = []
    for suite, case, unique in (
        ("metrics", "metric-compatibility", 200),
        ("epochs", "burst-single-epoch", 2000),
        ("epochs", "sustained-across-epochs", 600),
        ("scale", "scale-100", 100),
        ("scale", "scale-1000", 1000),
        ("offenders", "multiple-offenders", 1000),
    ):
        for mode in MODES:
            for repetition in (1, 2, 3):
                runs.append(run(suite, case, mode, repetition, unique))
    return {
        "results_version": 4,
        "platform": "test",
        "versions": {"collector": "0.162.0"},
        "parameters": {
            "suites": ["metrics", "epochs", "scale", "offenders"],
            "modes": MODES,
            "repetitions": 3,
            "threshold": 20,
            "epoch_seconds": 10,
            "large_scale": False,
        },
        "cases": [],
        "runs": runs,
        "summary": [],
    }


def test_stats_reports_median_and_range() -> None:
    assert stats([1.0, 5.0, 3.0]) == {
        "median": 3.0,
        "min": 1.0,
        "max": 5.0,
        "samples": [1.0, 5.0, 3.0],
    }
    assert stats([]) == {"median": None, "min": None, "max": None, "samples": []}


def test_cell_hides_the_range_when_repetitions_agree() -> None:
    assert cell(stats([21.0, 21.0])) == "21"
    assert cell(stats([20.0, 22.0])) == "21 (20-22)"
    assert cell(None) == "not run"
    assert percent(stats([0.895])) == "89.5%"


def test_summary_groups_by_suite_case_mode_and_metric(payload: dict) -> None:
    summary = summarize(payload)
    assert len(summary) == 6 * len(MODES)
    assert all(item["repetitions"] == 3 for item in summary)
    strip = find(
        summary,
        suite="scale",
        case="scale-1000",
        mode="strip_and_reaggregate",
    )
    assert strip[0]["output_identity_series"]["median"] == 21
    assert strip[0]["reduction"]["median"] == pytest.approx(0.979)


def histogram_observation(strategy: str) -> dict[str, Any]:
    native = strategy == "exponential-native"
    identity = {"baseline": 200, "sdk-view": 20, "filter": 0}.get(strategy, 200)
    per_identity = 1 if native else 11
    base = "lab_bench_delta_histogram"
    answers = {
        "total_requests": 600.0,
        "p95_by_method": 9.5,
        "requests_by_status": 120.0,
        "p95_for_one_user": None if strategy == "sdk-view" else 6.5,
    }
    shape = "native" if native else "classic"
    return {
        "identity_family": base if native else f"{base}_count",
        "representation": "absent" if identity == 0 else shape,
        "input_identity_series": 200,
        "output_identity_series": identity,
        "reduction": round(1 - identity / 200, 6),
        "family_series": {f"{base}_count": identity},
        "total_family_series": identity * per_identity,
        "series_by_job": {"lab-metrics": 220, "lab-overflow": 1980}
        if strategy == "routing"
        else {"lab-metrics": identity * per_identity},
        "questions": {
            name: {
                "query": f"sum({base}_count)",
                "value": None if identity == 0 else value,
                "error": None,
            }
            for name, value in answers.items()
        },
        "reaggregation_supported": False,
        "prometheus_shape": "classic histogram",
        "labels": {
            "series": identity,
            "distinct_label_values": {"user_id": identity},
            "overflow_marker_series": 0,
            "overflow_sentinel_series": 0,
        },
    }


@pytest.fixture
def histogram_payload(payload: dict[str, Any]) -> dict[str, Any]:
    strategies = [
        "baseline",
        "transform-delete",
        "sdk-view",
        "filter",
        "routing",
        "exponential-native",
    ]
    for strategy in strategies:
        for repetition in (1, 2, 3):
            item = run("histograms", "histogram-reduction", strategy, repetition, 200)
            item["metrics"] = {"delta_histogram": histogram_observation(strategy)}
            dropped = 11 if strategy == "transform-delete" else 0
            item["collector_log"] = {
                "warn_lines": 2 * dropped,
                "error_lines": 0,
                "dropped_histogram_datapoints_logged": dropped,
            }
            payload["runs"].append(item)
    payload["parameters"]["suites"].append("histograms")
    payload["cases"] = [
        {"name": "histogram-reduction", "strategies": strategies},
    ]
    payload["summary"] = summarize(payload)
    return payload


def test_histogram_section_lists_every_strategy_as_a_row(
    histogram_payload: dict,
    tmp_path: Path,
) -> None:
    report = render_markdown(histogram_payload, tmp_path / "benchmark")
    assert "## Histogram reduction" in report
    for strategy in ("baseline", "sdk-view", "filter", "exponential-native"):
        assert f"| {strategy} | " in report


def test_histogram_section_reports_a_lost_question_as_null(
    histogram_payload: dict,
    tmp_path: Path,
) -> None:
    report = render_markdown(histogram_payload, tmp_path / "benchmark")
    assert "| sdk-view | classic | 20 | 220 | 600 | 9.500 | 120 | null |" in report
    assert "| filter | absent | 0 | 0 | null | null | null | null |" in report


def test_histogram_section_separates_a_second_exporter(
    histogram_payload: dict,
    tmp_path: Path,
) -> None:
    report = render_markdown(histogram_payload, tmp_path / "benchmark")
    assert "| strategy | lab-metrics | lab-overflow |" in report
    assert "| routing | 220 | 1980 |" in report


def test_histogram_section_reports_the_collector_warnings_per_strategy(
    histogram_payload: dict,
    tmp_path: Path,
) -> None:
    report = render_markdown(histogram_payload, tmp_path / "benchmark")
    assert "| transform-delete | 22 | 0 | 11 |" in report
    assert "`baseline`, `sdk-view`, `filter`, `routing`, `exponential-native`" in report
    assert "wrote no line at `warn` level or above" in report


def test_histogram_section_says_so_when_no_strategy_logged_a_warning(
    histogram_payload: dict,
    tmp_path: Path,
) -> None:
    for item in histogram_payload["runs"]:
        if item["suite"] == "histograms":
            item["collector_log"] = dict.fromkeys(item["collector_log"], 0)
    histogram_payload["summary"] = summarize(histogram_payload)
    report = render_markdown(histogram_payload, tmp_path / "benchmark")
    assert "Collector log: no strategy wrote a line at `warn` level or above" in report
    assert "| strategy | warn lines |" not in report


def test_a_recording_without_collector_logs_renders_without_the_log_table(
    histogram_payload: dict,
    tmp_path: Path,
) -> None:
    for item in histogram_payload["runs"]:
        item.pop("collector_log", None)
    histogram_payload["summary"] = summarize(histogram_payload)
    report = render_markdown(histogram_payload, tmp_path / "benchmark")
    assert "## Histogram reduction" in report
    assert "Collector log" not in report
    assert "warn lines" not in report


def test_a_benchmark_report_without_the_suite_omits_the_section(
    payload: dict,
    tmp_path: Path,
) -> None:
    payload["summary"] = summarize(payload)
    report = render_markdown(payload, tmp_path / "benchmark")
    assert "## Histogram reduction" not in report


def test_a_histogram_run_writes_its_own_report(
    histogram_payload: dict,
    tmp_path: Path,
) -> None:
    histogram_payload["parameters"]["suites"] = ["histograms"]
    histogram_payload["parameters"]["modes"] = ["baseline", "sdk-view"]
    report = render_markdown(histogram_payload, tmp_path / "histograms")
    assert report.startswith("# Histogram reduction benchmark")
    assert "- Strategies: baseline, sdk-view" in report
    assert "## Metric compatibility" not in report
    assert "## Correctness" not in report
    assert "## Histogram reduction" in report


def test_report_contains_every_required_section(payload: dict, tmp_path: Path) -> None:
    payload["summary"] = summarize(payload)
    report = render_markdown(payload, tmp_path / "benchmark")
    for heading in (
        "## Correctness",
        "## Metric compatibility",
        "## Epoch behavior",
        "## Scale",
        "## Multiple offenders",
        "## Workload execution",
        "## Limitations",
    ):
        assert heading in report


def test_report_separates_facts_from_interpretation(
    payload: dict,
    tmp_path: Path,
) -> None:
    payload["summary"] = summarize(payload)
    report = render_markdown(payload, tmp_path / "benchmark")
    assert report.count("Measured fact:") >= 4
    assert report.count("Interpretation:") >= 4
    assert "Docker CPU and memory samples are diagnostic context only." in report


def test_report_renders_per_phase_progression(payload: dict, tmp_path: Path) -> None:
    payload["summary"] = summarize(payload)
    report = render_markdown(payload, tmp_path / "benchmark")
    assert "| mode | epoch-1 | epoch-2 | epoch-3 |" in report


def test_report_summarizes_phase_repetitions(payload: dict, tmp_path: Path) -> None:
    run = next(
        item
        for item in payload["runs"]
        if item["case"] == "sustained-across-epochs"
        and item["mode"] == "baseline"
        and item["repetition"] == 3
    )
    run["sending"]["phases"][1]["output_identity_series"] = 450
    payload["summary"] = summarize(payload)
    report = render_markdown(payload, tmp_path / "benchmark")
    assert "| baseline | 200 | 400 (400-450) | 600 |" in report


def test_report_describes_tracker_semantics_accurately(
    payload: dict,
    tmp_path: Path,
) -> None:
    payload["summary"] = summarize(payload)
    report = render_markdown(payload, tmp_path / "benchmark")
    assert "current-epoch cardinality estimate" in report
    assert "tracks each attribute key independently within a metric" in report
    assert "spends a fresh budget every epoch" not in report
    assert "tracks cardinality per metric, not per attribute" not in report


def test_report_marks_missing_suites_as_not_run(payload: dict, tmp_path: Path) -> None:
    payload["runs"] = [item for item in payload["runs"] if item["suite"] != "scale"]
    payload["summary"] = summarize(payload)
    report = render_markdown(payload, tmp_path / "benchmark")
    assert "## Scale\n\nNot run." in report


def test_report_notes_invalid_runs(payload: dict, tmp_path: Path) -> None:
    payload["runs"][0]["validity"] = {"ok": False, "notes": ["sender ran behind"]}
    payload["summary"] = summarize(payload)
    report = render_markdown(payload, tmp_path / "benchmark")
    assert "failed a validity check" in report


def test_report_names_unavailable_counters(payload: dict, tmp_path: Path) -> None:
    payload["runs"][0]["validity"]["unavailable_counters"] = [
        "otelcol_exporter_send_failed_metric_points"
    ]
    payload["summary"] = summarize(payload)
    report = render_markdown(payload, tmp_path / "benchmark")
    assert "did not expose these counters" in report
    assert "`otelcol_exporter_send_failed_metric_points`" in report
    assert "records them as `null`, not zero" in report
