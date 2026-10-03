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
