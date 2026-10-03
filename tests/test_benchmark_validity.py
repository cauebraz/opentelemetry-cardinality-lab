import pytest

from cardinality_lab.benchmark import scale_cases, validity
from cardinality_lab.prometheus import COLLECTOR_COUNTERS


def sending() -> dict:
    return {
        "sent_datapoints": 100,
        "rejected_datapoints": 0,
        "plan_duration_seconds": 0.0,
        "schedule_lag_seconds": 0.0,
    }


def counters() -> dict[str, float | None]:
    values = dict.fromkeys(COLLECTOR_COUNTERS)
    values["otelcol_receiver_accepted_metric_points"] = 100.0
    values["otelcol_receiver_refused_metric_points"] = 0.0
    values["otelcol_exporter_sent_metric_points"] = 100.0
    return values


def test_validity_accepts_complete_delivery_with_optional_counters_missing() -> None:
    result = validity(scale_cases((100,))[0], sending(), counters())
    assert result["ok"] is True
    assert result["notes"] == []
    assert result["unavailable_counters"] == [
        "otelcol_exporter_send_failed_metric_points",
        "otelcol_processor_incoming_items",
        "otelcol_processor_outgoing_items",
    ]


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        (
            "otelcol_receiver_accepted_metric_points",
            99.0,
            "receiver accepted 99 datapoints, sent 100",
        ),
        (
            "otelcol_receiver_refused_metric_points",
            1.0,
            "receiver refused 1 datapoints",
        ),
        (
            "otelcol_exporter_sent_metric_points",
            101.0,
            "exporter sent 101 datapoints after accepting 100",
        ),
        (
            "otelcol_exporter_send_failed_metric_points",
            1.0,
            "exporter failed to send 1 datapoints",
        ),
    ],
)
def test_validity_rejects_counter_failures(
    name: str,
    value: float,
    message: str,
) -> None:
    observed = counters()
    observed[name] = value
    result = validity(scale_cases((100,))[0], sending(), observed)
    assert result["ok"] is False
    assert message in result["notes"]


@pytest.mark.parametrize(
    "name",
    [
        "otelcol_receiver_accepted_metric_points",
        "otelcol_receiver_refused_metric_points",
        "otelcol_exporter_sent_metric_points",
    ],
)
def test_validity_requires_delivery_counters(name: str) -> None:
    observed = counters()
    observed[name] = None
    result = validity(scale_cases((100,))[0], sending(), observed)
    assert result["ok"] is False
    assert any("unavailable" in note for note in result["notes"])
