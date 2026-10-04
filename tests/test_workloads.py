import pytest

from cardinality_lab.workloads import (
    BOUNDED,
    WORKLOADS,
    WORKLOADS_BY_NAME,
    Workload,
    burst_plan,
    plan_duration_seconds,
    plan_unique_values,
    sustained_plan,
)


def identity(attributes) -> tuple:
    return tuple(sorted(attributes.items()))


@pytest.mark.parametrize("workload", WORKLOADS, ids=lambda item: item.name)
def test_every_index_produces_a_distinct_identity(workload: Workload) -> None:
    sets = workload.attribute_sets(0, 500)
    assert len({identity(attributes) for attributes in sets}) == 500


@pytest.mark.parametrize("workload", WORKLOADS, ids=lambda item: item.name)
def test_bounded_attributes_stay_bounded(workload: Workload) -> None:
    sets = workload.attribute_sets(0, 500)
    for key, value in BOUNDED.items():
        assert {attributes[key] for attributes in sets} == {value}
    assert len({attributes["http.method"] for attributes in sets}) == 4
    if "http.status_code" in sets[0]:
        assert len({attributes["http.status_code"] for attributes in sets}) == 5


@pytest.mark.parametrize("workload", WORKLOADS, ids=lambda item: item.name)
def test_declared_unbounded_attributes_grow_with_the_index(workload: Workload) -> None:
    sets = workload.attribute_sets(0, 500)
    for key in workload.unbounded_attributes:
        assert len({attributes[key] for attributes in sets}) == 500


def test_multiple_offenders_declares_four_correlated_attributes() -> None:
    workload = WORKLOADS_BY_NAME["multiple-offenders"]
    assert workload.unbounded_attributes == (
        "user.id",
        "request.id",
        "http.route",
        "customer.id",
    )
    assert len(workload.attribute_sets(0, 100)) == 100


def test_the_histogram_workload_pairs_every_method_with_every_status() -> None:
    workload = WORKLOADS_BY_NAME["histogram-user-id"]
    sets = workload.attribute_sets(0, 200)
    pairs = {
        (attributes["http.method"], attributes["http.status_code"])
        for attributes in sets
    }
    assert len(pairs) == 20
    assert workload.unbounded_attributes == ("user.id",)


def test_attribute_sets_respect_the_first_index() -> None:
    workload = WORKLOADS_BY_NAME["single-offender"]
    assert workload.attribute_sets(10, 2)[0]["user.id"] == "user-000010"


def test_burst_plan_sends_everything_at_offset_zero() -> None:
    batches = burst_plan(5000, batch_size=2000)
    assert [batch.count for batch in batches] == [2000, 2000, 1000]
    assert {batch.start_offset_seconds for batch in batches} == {0.0}
    assert plan_unique_values(batches) == 5000
    assert plan_duration_seconds(batches) == 0.0


def test_sustained_plan_spreads_each_epoch() -> None:
    batches = sustained_plan(
        unique_values_per_epoch=40,
        epochs=3,
        epoch_seconds=10,
        batches_per_epoch=4,
    )
    assert len(batches) == 12
    assert plan_unique_values(batches) == 120
    assert sorted({batch.phase for batch in batches}) == [
        "epoch-1",
        "epoch-2",
        "epoch-3",
    ]
    assert [batch.start_offset_seconds for batch in batches[:4]] == [
        0.0,
        2.5,
        5.0,
        7.5,
    ]
    assert batches[4].start_offset_seconds == 10.0
    assert batches[4].first_index == 40


def test_sustained_plan_rejects_degenerate_arguments() -> None:
    with pytest.raises(ValueError):
        sustained_plan(10, epochs=0, epoch_seconds=10)
    with pytest.raises(ValueError):
        sustained_plan(10, epochs=1, epoch_seconds=10, batches_per_epoch=0)
