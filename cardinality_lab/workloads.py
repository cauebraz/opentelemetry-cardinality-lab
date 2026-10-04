from collections.abc import Callable, Mapping
from dataclasses import dataclass

MAX_BATCH_DATAPOINTS = 2000
METHODS = ("GET", "POST", "PUT", "DELETE")
STATUS_CODES = ("200", "201", "400", "404", "500")
BOUNDED = {"environment": "lab", "region": "local"}


def single_offender(index: int) -> Mapping[str, str]:
    return BOUNDED | {
        "http.method": METHODS[index % len(METHODS)],
        "user.id": f"user-{index:06d}",
    }


def multiple_offenders(index: int) -> Mapping[str, str]:
    return BOUNDED | {
        "http.method": METHODS[index % len(METHODS)],
        "http.status_code": STATUS_CODES[index % len(STATUS_CODES)],
        "http.route": f"/orders/order-{index:06d}",
        "user.id": f"user-{index:06d}",
        "request.id": f"req-{index:012d}",
        "customer.id": f"customer-{index:06d}",
    }


def histogram_user_id(index: int) -> Mapping[str, str]:
    return BOUNDED | {
        "http.method": METHODS[index % len(METHODS)],
        "http.status_code": STATUS_CODES[index % len(STATUS_CODES)],
        "user.id": f"user-{index:06d}",
    }


@dataclass(frozen=True)
class Workload:
    name: str
    description: str
    builder: Callable[[int], Mapping[str, str]]
    unbounded_attributes: tuple[str, ...]

    def attribute_sets(
        self,
        first_index: int,
        count: int,
    ) -> list[Mapping[str, str]]:
        last_index = first_index + count
        return [self.builder(index) for index in range(first_index, last_index)]


WORKLOADS: tuple[Workload, ...] = (
    Workload(
        name="single-offender",
        description="One unbounded attribute (user.id) beside bounded attributes.",
        builder=single_offender,
        unbounded_attributes=("user.id",),
    ),
    Workload(
        name="multiple-offenders",
        description=(
            "Four correlated unbounded attributes (user.id, request.id, "
            "http.route, customer.id) beside bounded method and status."
        ),
        builder=multiple_offenders,
        unbounded_attributes=(
            "user.id",
            "request.id",
            "http.route",
            "customer.id",
        ),
    ),
    Workload(
        name="histogram-user-id",
        description=(
            "One unbounded attribute (user.id) beside a bounded method and "
            "status code, for the histogram reduction strategies."
        ),
        builder=histogram_user_id,
        unbounded_attributes=("user.id",),
    ),
)

WORKLOADS_BY_NAME = {workload.name: workload for workload in WORKLOADS}


@dataclass(frozen=True)
class Batch:
    phase: str
    start_offset_seconds: float
    first_index: int
    count: int


def _split(
    phase: str,
    first_index: int,
    count: int,
    start_offset: float,
    window_seconds: float,
    batch_size: int,
) -> list[Batch]:
    if count <= 0:
        return []
    batches = []
    sent = 0
    total_batches = -(-count // batch_size)
    for position in range(total_batches):
        size = min(batch_size, count - sent)
        offset = start_offset + window_seconds * position / total_batches
        batches.append(Batch(phase, offset, first_index + sent, size))
        sent += size
    return batches


def burst_plan(
    unique_values: int,
    batch_size: int = MAX_BATCH_DATAPOINTS,
) -> list[Batch]:
    """Every unique value as fast as the sender allows, inside one epoch."""
    return _split("burst", 0, unique_values, 0.0, 0.0, batch_size)


def sustained_plan(
    unique_values_per_epoch: int,
    epochs: int,
    epoch_seconds: int,
    batches_per_epoch: int = 4,
) -> list[Batch]:
    """A controlled creation rate held across consecutive epochs."""
    if epochs < 1:
        raise ValueError("epochs must be at least 1")
    if batches_per_epoch < 1:
        raise ValueError("batches_per_epoch must be at least 1")
    batch_size = -(-unique_values_per_epoch // batches_per_epoch)
    batches: list[Batch] = []
    for epoch in range(epochs):
        batches.extend(
            _split(
                f"epoch-{epoch + 1}",
                epoch * unique_values_per_epoch,
                unique_values_per_epoch,
                epoch * epoch_seconds,
                epoch_seconds,
                batch_size,
            )
        )
    return batches


def plan_unique_values(batches: list[Batch]) -> int:
    return len({index for batch in batches for index in _indices(batch)})


def _indices(batch: Batch) -> range:
    return range(batch.first_index, batch.first_index + batch.count)


def plan_duration_seconds(batches: list[Batch]) -> float:
    return max((batch.start_offset_seconds for batch in batches), default=0.0)
