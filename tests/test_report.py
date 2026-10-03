from cardinality_lab.report import render_markdown, summarize


def payload() -> dict:
    runs = []
    for mode in ("tag_only", "overflow_attribute", "strip_and_reaggregate"):
        dynamic_series = {
            "tag_only": 200,
            "overflow_attribute": 21,
            "strip_and_reaggregate": 20,
        }[mode]
        runs.append(
            {
                "mode": mode,
                "repetition": 1,
                "series": {
                    "stable": 4,
                    "user-id": 200 if mode == "tag_only" else 21,
                    "unbounded-route": 200 if mode == "tag_only" else 21,
                    "histogram": 200,
                },
                "labels": {
                    "stable": {
                        "dynamic_label_series": 0,
                        "overflow_marker_series": 0,
                    },
                    "user-id": {
                        "dynamic_label_series": dynamic_series,
                        "overflow_marker_series": 180 if mode == "tag_only" else 0,
                    },
                    "unbounded-route": {
                        "dynamic_label_series": dynamic_series,
                        "overflow_marker_series": 180 if mode == "tag_only" else 0,
                    },
                    "histogram": {
                        "dynamic_label_series": 200,
                        "overflow_marker_series": 180,
                    },
                },
                "self_metrics": {"otelcol_processor_cardinality_labels_stripped": 10.0},
            }
        )
    return {
        "collected_at": "2026-10-03T01:00:00+00:00",
        "versions": {
            "collector": "0.162.0",
            "prometheus": "3.15.0",
            "otel_python": "1.45.0",
        },
        "parameters": {
            "points": 200,
            "repetitions": 1,
            "threshold": 20,
            "epoch_seconds": 10,
        },
        "modes": [
            "tag_only",
            "overflow_attribute",
            "strip_and_reaggregate",
        ],
        "runs": runs,
    }


def test_summarize_groups_series_by_mode() -> None:
    grouped = summarize(payload())
    assert grouped["overflow_attribute"]["user-id"] == [21]


def test_report_contains_measured_results() -> None:
    report = render_markdown(payload())
    assert "| tag_only | 4 | 200 | 200 | 200 |" in report
    assert "| strip_and_reaggregate | user-id | 20 | 0 |" in report
    assert "Collected at: 2026-10-03T01:00:00+00:00" in report
    assert "not vendor pricing estimates" in report
