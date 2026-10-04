import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from math import isnan
from typing import Any


def read_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.load(response)


def wait_for_url(url: str, timeout_seconds: float = 30) -> None:
    deadline = time.monotonic() + timeout_seconds
    error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 400:
                    return
        except (OSError, urllib.error.URLError) as caught:
            error = caught
        time.sleep(0.25)
    raise TimeoutError(f"{url} was not ready after {timeout_seconds}s: {error}")


def query_scalar(base_url: str, query: str) -> float:
    result = query_result(base_url, query)
    if not result:
        return 0
    return float(result[0]["value"][1])


def query_value(base_url: str, query: str) -> float | None:
    """The first sample of a query, or None when it returned nothing usable."""
    result = query_result(base_url, query)
    if not result:
        return None
    value = float(result[0]["value"][1])
    return None if isnan(value) else value


def query_result(base_url: str, query: str) -> list[dict[str, Any]]:
    encoded = urllib.parse.urlencode({"query": query})
    payload = read_json(f"{base_url}/api/v1/query?{encoded}")
    return payload["data"]["result"]


def wait_for_series(
    base_url: str,
    metric_name: str,
    minimum: int = 1,
    timeout_seconds: float = 15,
) -> int:
    deadline = time.monotonic() + timeout_seconds
    count = 0
    while time.monotonic() < deadline:
        count = round(query_scalar(base_url, f"count({metric_name})"))
        if count >= minimum:
            return count
        time.sleep(0.5)
    raise TimeoutError(
        f"{metric_name} exposed {count} series, expected at least {minimum}"
    )


def series_count(base_url: str, family: str) -> int:
    """Series currently present in one Prometheus metric family."""
    return round(query_scalar(base_url, f'count({{__name__="{family}"}})'))


def families_series_count(base_url: str, families: Sequence[str]) -> dict[str, int]:
    return {family: series_count(base_url, family) for family in families}


def series_count_by_job(
    base_url: str,
    families: Sequence[str],
    jobs: Sequence[str],
) -> dict[str, int]:
    """Series per scrape job, so a second exporter is not read as a reduction."""
    selector = "|".join(families)
    return {
        job: round(
            query_scalar(
                base_url,
                f'count({{__name__=~"{selector}", job="{job}"}})',
            )
        )
        for job in jobs
    }


def label_values(base_url: str, family: str, label: str) -> list[str]:
    results = query_result(base_url, f'{{__name__="{family}"}}')
    return sorted(
        {result["metric"][label] for result in results if label in result["metric"]}
    )


def label_profile(base_url: str, family: str, labels: Sequence[str]) -> dict[str, Any]:
    results = query_result(base_url, f'{{__name__="{family}"}}')
    metrics = [result["metric"] for result in results]
    return {
        "series": len(metrics),
        "distinct_label_values": {
            label: len({metric[label] for metric in metrics if label in metric})
            for label in labels
        },
        "overflow_marker_series": sum(
            metric.get("otel_metric_overflow") == "true" for metric in metrics
        ),
        "overflow_sentinel_series": sum(
            any(
                value == "otel.cardinality_overflow"
                for key, value in metric.items()
                if key != "__name__"
            )
            for metric in metrics
        ),
    }


def wait_for_accepted_points(
    base_url: str,
    minimum: float = 1.0,
    timeout_seconds: float = 60.0,
) -> float:
    """Wait for the Collector to report that it received the workload."""
    deadline = time.monotonic() + timeout_seconds
    accepted: float | None = None
    while time.monotonic() < deadline:
        accepted = collector_counters(base_url)[
            "otelcol_receiver_accepted_metric_points"
        ]
        if accepted is not None and accepted >= minimum:
            return accepted
        time.sleep(0.5)
    raise TimeoutError(
        f"the collector reported {accepted} accepted metric points, "
        f"expected at least {minimum}"
    )


def wait_until_stable(
    base_url: str,
    families: Sequence[str],
    stable_scrapes: int = 3,
    poll_seconds: float = 1.0,
    timeout_seconds: float = 120.0,
    require_nonzero: bool = True,
) -> dict[str, int]:
    """Poll until the family series counts stop changing, instead of sleeping."""
    deadline = time.monotonic() + timeout_seconds
    previous: dict[str, int] | None = None
    repeats = 0
    counts: dict[str, int] = {}
    while time.monotonic() < deadline:
        counts = families_series_count(base_url, families)
        if counts == previous and (any(counts.values()) or not require_nonzero):
            repeats += 1
            if repeats >= stable_scrapes:
                return counts
        else:
            repeats = 0
        previous = counts
        time.sleep(poll_seconds)
    raise TimeoutError(
        f"series counts for {list(families)} did not settle within "
        f"{timeout_seconds}s; last observation was {counts}"
    )


COLLECTOR_COUNTERS = (
    "otelcol_receiver_accepted_metric_points",
    "otelcol_receiver_refused_metric_points",
    "otelcol_exporter_sent_metric_points",
    "otelcol_exporter_send_failed_metric_points",
    "otelcol_processor_incoming_items",
    "otelcol_processor_outgoing_items",
)

COUNTER_SELECTORS = {
    "otelcol_receiver_accepted_metric_points": ', receiver="otlp"',
    "otelcol_receiver_refused_metric_points": ', receiver="otlp"',
    "otelcol_exporter_sent_metric_points": ', exporter=~"prometheus.*"',
    "otelcol_exporter_send_failed_metric_points": ', exporter=~"prometheus.*"',
}


def collector_counters(base_url: str) -> dict[str, float | None]:
    """Delivery counters, scoped so a connector is not counted as a pipeline end."""
    payload = read_json(f"{base_url}/api/v1/label/__name__/values")
    available = set(payload["data"])
    counters: dict[str, float | None] = {}
    for name in COLLECTOR_COUNTERS:
        counters[name] = None
        selector = COUNTER_SELECTORS.get(name, "")
        for candidate in (name, f"{name}_total"):
            if candidate in available:
                counters[name] = query_scalar(
                    base_url,
                    f'sum({{__name__="{candidate}"{selector}}})',
                )
                break
    return counters


def cardinality_self_metrics(base_url: str) -> dict[str, float]:
    payload = read_json(f"{base_url}/api/v1/label/__name__/values")
    names = sorted(name for name in payload["data"] if "processor_cardinality" in name)
    return {
        name: query_scalar(
            base_url,
            f'sum({{__name__="{name}"}})',
        )
        for name in names
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("expression")
    parser.add_argument("--url", default="http://127.0.0.1:19090")
    args = parser.parse_args()
    print(json.dumps(query_result(args.url, args.expression), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
