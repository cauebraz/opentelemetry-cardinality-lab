import pytest

from cardinality_lab.scenarios import SCENARIOS_BY_NAME


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("stable", 4),
        ("user-id", 200),
        ("unbounded-route", 200),
        ("histogram", 200),
    ],
)
def test_attribute_cardinality(scenario: str, expected: int) -> None:
    definition = SCENARIOS_BY_NAME[scenario]
    attributes = {
        tuple(sorted(definition.attributes(index).items())) for index in range(200)
    }
    assert len(attributes) == expected


@pytest.mark.parametrize(
    ("scenario", "mode", "expected"),
    [
        ("stable", "tag_only", 4),
        ("stable", "overflow_attribute", 4),
        ("user-id", "tag_only", 200),
        ("user-id", "overflow_attribute", 21),
        ("unbounded-route", "strip_and_reaggregate", 21),
        ("histogram", "overflow_attribute", 200),
        ("histogram", "strip_and_reaggregate", 200),
    ],
)
def test_expected_series(scenario: str, mode: str, expected: int) -> None:
    definition = SCENARIOS_BY_NAME[scenario]
    assert definition.expected_series(mode, points=200, threshold=20) == expected


@pytest.mark.parametrize(
    ("scenario", "mode", "dynamic", "marked", "values"),
    [
        ("user-id", "tag_only", 200, 180, []),
        (
            "user-id",
            "overflow_attribute",
            21,
            0,
            ["otel.cardinality_overflow"],
        ),
        ("user-id", "strip_and_reaggregate", 20, 0, []),
        ("histogram", "overflow_attribute", 200, 180, []),
    ],
)
def test_expected_label_profile(
    scenario: str,
    mode: str,
    dynamic: int,
    marked: int,
    values: list[str],
) -> None:
    definition = SCENARIOS_BY_NAME[scenario]
    assert definition.expected_dynamic_label_series(mode, 200, 20) == dynamic
    assert definition.expected_overflow_marker_series(mode, 200, 20) == marked
    assert definition.expected_overflow_values(mode) == values
