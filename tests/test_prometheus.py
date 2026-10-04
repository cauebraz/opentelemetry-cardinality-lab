from itertools import chain, repeat

import pytest

from cardinality_lab import prometheus


def test_collector_counters_distinguish_zero_from_unavailable(monkeypatch) -> None:
    available = {
        "otelcol_receiver_accepted_metric_points",
        "otelcol_receiver_refused_metric_points_total",
    }
    values = {
        "otelcol_receiver_accepted_metric_points": 100.0,
        "otelcol_receiver_refused_metric_points_total": 0.0,
    }
    monkeypatch.setattr(
        prometheus,
        "read_json",
        lambda _: {"data": sorted(available)},
    )
    monkeypatch.setattr(
        prometheus,
        "query_scalar",
        lambda _, query: next(
            value for name, value in values.items() if f'"{name}"' in query
        ),
    )

    counters = prometheus.collector_counters("http://prometheus")

    assert counters["otelcol_receiver_accepted_metric_points"] == 100.0
    assert counters["otelcol_receiver_refused_metric_points"] == 0.0
    assert counters["otelcol_exporter_sent_metric_points"] is None
    assert set(counters) == set(prometheus.COLLECTOR_COUNTERS)


@pytest.mark.parametrize(
    ("require_nonzero", "expected"),
    [(True, 5), (False, 0)],
)
def test_a_late_export_settles_as_zero_only_when_zero_is_allowed(
    monkeypatch,
    require_nonzero: bool,
    expected: int,
) -> None:
    counts = chain([0, 0, 0, 0], repeat(5))
    monkeypatch.setattr(
        prometheus,
        "families_series_count",
        lambda _, families: dict.fromkeys(families, next(counts)),
    )
    monkeypatch.setattr(prometheus.time, "sleep", lambda _: None)

    settled = prometheus.wait_until_stable(
        "http://prometheus",
        ["lab_bench_delta_histogram_count"],
        poll_seconds=0.0,
        require_nonzero=require_nonzero,
    )

    assert settled == {"lab_bench_delta_histogram_count": expected}
