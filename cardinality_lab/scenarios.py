from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

MetricKind = Literal["counter", "histogram"]


@dataclass(frozen=True)
class Scenario:
    name: str
    metric_name: str
    prometheus_name: str
    kind: MetricKind
    dynamic_attribute: str | None

    @property
    def prometheus_dynamic_attribute(self) -> str | None:
        if self.dynamic_attribute is None:
            return None
        return self.dynamic_attribute.replace(".", "_")

    def attributes(self, index: int) -> Mapping[str, str]:
        common = {"environment": "lab", "region": "local"}
        if self.name == "stable":
            return common | {"http.route": f"/fixed/{index % 4}"}
        if self.name == "user-id":
            return common | {"user.id": f"user-{index:06d}"}
        if self.name == "unbounded-route":
            return common | {"http.route": f"/orders/order-{index:06d}"}
        return common | {"user.id": f"user-{index:06d}"}

    def expected_series(self, mode: str, points: int, threshold: int) -> int:
        if self.name == "stable":
            return min(points, 4)
        if self.kind == "histogram" or mode == "tag_only":
            return points
        return min(points, threshold + 1)

    def expected_dynamic_label_series(
        self,
        mode: str,
        points: int,
        threshold: int,
    ) -> int:
        if self.dynamic_attribute is None:
            return 0
        if self.kind == "histogram" or mode == "tag_only":
            return points
        if mode == "overflow_attribute":
            return min(points, threshold + 1)
        return min(points, threshold)

    def expected_overflow_marker_series(
        self,
        mode: str,
        points: int,
        threshold: int,
    ) -> int:
        if self.dynamic_attribute is None:
            return 0
        if self.kind == "histogram" or mode == "tag_only":
            return max(points - threshold, 0)
        return 0

    def expected_overflow_values(self, mode: str) -> list[str]:
        if (
            self.dynamic_attribute is not None
            and self.kind == "counter"
            and mode == "overflow_attribute"
        ):
            return ["otel.cardinality_overflow"]
        return []


SCENARIOS = (
    Scenario(
        name="stable",
        metric_name="lab.stable.requests",
        prometheus_name="lab_stable_requests_total",
        kind="counter",
        dynamic_attribute=None,
    ),
    Scenario(
        name="user-id",
        metric_name="lab.user_id.requests",
        prometheus_name="lab_user_id_requests_total",
        kind="counter",
        dynamic_attribute="user.id",
    ),
    Scenario(
        name="unbounded-route",
        metric_name="lab.route.requests",
        prometheus_name="lab_route_requests_total",
        kind="counter",
        dynamic_attribute="http.route",
    ),
    Scenario(
        name="histogram",
        metric_name="lab.request.duration",
        prometheus_name="lab_request_duration_milliseconds_count",
        kind="histogram",
        dynamic_attribute="user.id",
    ),
)

SCENARIOS_BY_NAME = {scenario.name: scenario for scenario in SCENARIOS}
