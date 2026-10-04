from pathlib import Path

import pytest

from cardinality_lab.stack import (
    DROPPED_HISTOGRAM_DATAPOINT,
    LOG_TIMESTAMP,
    MODES,
    ROOT,
    STRATEGIES,
    STRATEGIES_BY_NAME,
    Strategy,
    environment_for,
    log_summary,
    strategy_for,
)


@pytest.mark.parametrize("strategy", STRATEGIES, ids=lambda item: item.name)
def test_every_strategy_points_at_files_that_exist(strategy: Strategy) -> None:
    for relative in (strategy.collector_config, strategy.prometheus_config):
        assert (ROOT / Path(relative)).is_file(), relative


def test_the_enforcement_modes_remain_selectable_by_name() -> None:
    for mode in MODES:
        assert mode in STRATEGIES_BY_NAME
    assert strategy_for("tag_only").enforcement_mode == "tag_only"
    assert strategy_for("baseline").collector_config.endswith("collector-baseline.yaml")


def test_the_environment_carries_both_configs() -> None:
    environment = environment_for("exponential-native", threshold=20, epoch_seconds=10)
    assert environment["COLLECTOR_CONFIG"] == "./config/collector-baseline.yaml"
    assert environment["PROMETHEUS_CONFIG"] == "./config/prometheus-native.yaml"
    assert environment["CARDINALITY_THRESHOLD"] == "20"
    assert environment["EPOCH_SECONDS"] == "10"


def test_an_unknown_strategy_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown mode"):
        environment_for("drop_everything")


def test_only_the_routing_strategy_reads_a_second_scrape_job() -> None:
    multi = [strategy.name for strategy in STRATEGIES if len(strategy.jobs) > 1]
    assert multi == ["routing", "routing-string"]


def test_collector_log_timestamps_are_removed() -> None:
    timestamp = f"{2_000:04d}-{1:02d}-{1:02d}T{0:02d}:{0:02d}:{0:02d}.000Z"
    line = (
        f"collector-1  | {timestamp}\tinfo\thealthcheckextension\tExtension started.\n"
    )
    assert LOG_TIMESTAMP.sub("", line) == (
        "collector-1  | info\thealthcheckextension\tExtension started.\n"
    )


def test_log_summary_counts_levels_and_the_dropped_datapoint_message() -> None:
    text = "\n".join(
        [
            "collector-1  | info\tservice/service.go:233\tStarting otelcol-contrib...",
            "collector-1  | warn\tprometheusexporter/accumulator.go:298\t"
            "Misaligned starting timestamps\t{}",
            f"collector-1  | warn\tprometheusexporter/accumulator.go:302\t"
            f"{DROPPED_HISTOGRAM_DATAPOINT}\t{{}}",
            "collector-1  | error\texporterhelper/queue.go:10\tExporting failed\t{}",
            "collector-1  | info\tservice/service.go:256\tEverything is ready.",
        ]
    )
    assert log_summary(text) == {
        "warn_lines": 2,
        "error_lines": 1,
        "dropped_histogram_datapoints_logged": 1,
    }


def test_log_summary_of_a_quiet_collector_is_all_zeros() -> None:
    text = "collector-1  | info\tservice/service.go:256\tEverything is ready.\n"
    assert log_summary(text) == {
        "warn_lines": 0,
        "error_lines": 0,
        "dropped_histogram_datapoints_logged": 0,
    }
