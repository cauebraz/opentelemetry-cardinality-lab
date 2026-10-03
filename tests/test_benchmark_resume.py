import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from cardinality_lab.benchmark import RESULTS_VERSION, resume_from

VERSIONS = {"collector": "0.162.0"}
CASES = [
    {
        "suite": "scale",
        "name": "scale-100",
        "description": "100 unique attribute values in one burst.",
        "workload": "single-offender",
        "metric_keys": ["delta_sum"],
        "unique_values": 100,
        "batches": 1,
        "plan_duration_seconds": 0.0,
    }
]


def recorded(**parameters: Any) -> dict[str, Any]:
    return {
        "results_version": RESULTS_VERSION,
        "platform": "test",
        "versions": dict(VERSIONS),
        "parameters": {
            "suites": ["scale"],
            "modes": ["baseline"],
            "threshold": 20,
            "epoch_seconds": 10,
            "repetitions": 3,
            "large_scale": False,
        }
        | parameters,
        "cases": deepcopy(CASES),
        "runs": [
            {"case": "scale-100", "mode": "baseline", "repetition": 1},
            {"case": "scale-100", "mode": "baseline", "repetition": 2},
        ],
    }


def fresh() -> dict[str, Any]:
    return {
        "platform": "test",
        "versions": dict(VERSIONS),
        "parameters": {
            "suites": ["scale"],
            "modes": ["baseline"],
            "threshold": 20,
            "epoch_seconds": 10,
            "repetitions": 3,
            "large_scale": False,
        },
        "cases": deepcopy(CASES),
        "runs": [],
    }


def write(tmp_path: Path, payload: dict[str, Any]) -> Path:
    source = tmp_path / "results.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    return source


def test_missing_artifact_starts_from_nothing(tmp_path: Path) -> None:
    payload, done = resume_from(tmp_path / "absent.json", fresh())
    assert payload["runs"] == []
    assert done == set()


def test_recorded_runs_are_reused_and_skipped(tmp_path: Path) -> None:
    payload, done = resume_from(write(tmp_path, recorded()), fresh())
    assert len(payload["runs"]) == 2
    assert done == {
        ("scale-100", "baseline", 1),
        ("scale-100", "baseline", 2),
    }
    assert "collected_at" not in payload
    assert "resumed_at" not in payload


@pytest.mark.parametrize(
    "parameters",
    [
        {"suites": ["metrics"]},
        {"modes": ["tag_only"]},
        {"threshold": 50},
        {"epoch_seconds": 30},
        {"repetitions": 1},
    ],
)
def test_resume_refuses_different_parameters(
    tmp_path: Path,
    parameters: dict[str, Any],
) -> None:
    with pytest.raises(ValueError, match="identical parameters"):
        resume_from(write(tmp_path, recorded(**parameters)), fresh())


def test_resume_refuses_an_older_results_version(tmp_path: Path) -> None:
    payload = recorded()
    payload["results_version"] = RESULTS_VERSION - 1
    with pytest.raises(ValueError, match="results_version"):
        resume_from(write(tmp_path, payload), fresh())


def test_resume_refuses_different_component_versions(tmp_path: Path) -> None:
    payload = recorded()
    payload["versions"] = {"collector": "0.163.0"}
    with pytest.raises(ValueError, match="component versions"):
        resume_from(write(tmp_path, payload), fresh())


def test_resume_refuses_different_case_definitions(tmp_path: Path) -> None:
    payload = recorded()
    payload["cases"][0]["unique_values"] = 200
    with pytest.raises(ValueError, match="case definitions"):
        resume_from(write(tmp_path, payload), fresh())


def test_resume_refuses_duplicate_run_keys(tmp_path: Path) -> None:
    payload = recorded()
    payload["runs"].append(payload["runs"][0])
    with pytest.raises(ValueError, match="duplicate run keys"):
        resume_from(write(tmp_path, payload), fresh())


def test_resume_refuses_runs_outside_the_requested_scope(tmp_path: Path) -> None:
    payload = recorded()
    payload["runs"].append({"case": "scale-1000", "mode": "baseline", "repetition": 1})
    with pytest.raises(ValueError, match="outside the requested benchmark scope"):
        resume_from(write(tmp_path, payload), fresh())
