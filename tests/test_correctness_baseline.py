import json

import pytest

from cardinality_lab.experiment import MODES, assertion_failures
from cardinality_lab.scenarios import SCENARIOS_BY_NAME
from cardinality_lab.stack import RESULTS

POINTS = 200
THRESHOLD = 20

EXPECTED: dict[tuple[str, str], tuple[int, int, int, list[str]]] = {
    ("stable", "tag_only"): (4, 0, 0, []),
    ("stable", "overflow_attribute"): (4, 0, 0, []),
    ("stable", "strip_and_reaggregate"): (4, 0, 0, []),
    ("user-id", "tag_only"): (200, 200, 180, []),
    ("user-id", "overflow_attribute"): (21, 21, 0, ["otel.cardinality_overflow"]),
    ("user-id", "strip_and_reaggregate"): (21, 20, 0, []),
    ("unbounded-route", "tag_only"): (200, 200, 180, []),
    ("unbounded-route", "overflow_attribute"): (
        21,
        21,
        0,
        ["otel.cardinality_overflow"],
    ),
    ("unbounded-route", "strip_and_reaggregate"): (21, 20, 0, []),
    ("histogram", "tag_only"): (200, 200, 180, []),
    ("histogram", "overflow_attribute"): (200, 200, 180, []),
    ("histogram", "strip_and_reaggregate"): (200, 200, 180, []),
}


@pytest.fixture(scope="module")
def recorded() -> dict:
    return json.loads((RESULTS / "results.json").read_text(encoding="utf-8"))


def test_matrix_covers_every_scenario_and_mode() -> None:
    assert set(EXPECTED) == {
        (name, mode) for name in SCENARIOS_BY_NAME for mode in MODES
    }


@pytest.mark.parametrize(("key", "expected"), sorted(EXPECTED.items()))
def test_oracle_matches_locked_matrix(
    key: tuple[str, str],
    expected: tuple[int, int, int, list[str]],
) -> None:
    name, mode = key
    scenario = SCENARIOS_BY_NAME[name]
    assert (
        scenario.expected_series(mode, POINTS, THRESHOLD),
        scenario.expected_dynamic_label_series(mode, POINTS, THRESHOLD),
        scenario.expected_overflow_marker_series(mode, POINTS, THRESHOLD),
        scenario.expected_overflow_values(mode),
    ) == expected


def test_recorded_parameters_match_the_baseline(recorded: dict) -> None:
    assert recorded["parameters"]["points"] == POINTS
    assert recorded["parameters"]["threshold"] == THRESHOLD
    assert recorded["modes"] == list(MODES)
    assert recorded["assertion_failures"] == []
    assert len(recorded["runs"]) == recorded["parameters"]["repetitions"] * len(MODES)


def test_recorded_measurements_match_the_locked_matrix(recorded: dict) -> None:
    for run in recorded["runs"]:
        for name, scenario in SCENARIOS_BY_NAME.items():
            series, dynamic, marked, values = EXPECTED[(name, run["mode"])]
            profile = run["labels"][name]
            observed = (
                run["series"][name],
                profile["dynamic_label_series"],
                profile["overflow_marker_series"],
                profile["overflow_values"],
            )
            assert observed == (series, dynamic, marked, values), (
                f"{run['mode']} repetition {run['repetition']} {scenario.name}"
            )


def test_assertions_still_pass_against_the_recorded_run(recorded: dict) -> None:
    assert assertion_failures(recorded) == []
