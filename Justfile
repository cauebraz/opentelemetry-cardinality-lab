set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

setup:
    uv sync --frozen

lock:
    uv lock

fix:
    uv run --frozen ruff check --fix cardinality_lab tests
    uv run --frozen ruff format cardinality_lab tests

format-check:
    uv run --frozen ruff format --check cardinality_lab tests

lint:
    uv run --frozen ruff check cardinality_lab tests

test:
    uv run --frozen pytest

build:
    docker compose build collector

validate: build
    docker compose config --quiet
    for config in ./config/collector.yaml ./config/collector-baseline.yaml ./config/collector-transform-delete.yaml ./config/collector-transform-aggregate.yaml ./config/collector-filter.yaml ./config/collector-routing.yaml ./config/collector-routing-string.yaml; do \
      COLLECTOR_CONFIG="${config}" docker compose run --rm --no-deps collector validate --config=/etc/otelcol-contrib/config.yaml; \
    done
    for config in ./config/prometheus.yaml ./config/prometheus-routing.yaml ./config/prometheus-native.yaml; do \
      PROMETHEUS_CONFIG="${config}" docker compose run --rm --no-deps --entrypoint promtool prometheus check config /etc/prometheus/prometheus.yaml; \
    done

check: format-check lint test validate

up mode="tag_only": build
    ENFORCEMENT_MODE='{{mode}}' docker compose up --detach

down:
    docker compose down --volumes --remove-orphans

emit scenario points="200":
    uv run --frozen python -m cardinality_lab.generate '{{scenario}}' --points '{{points}}'

query expression:
    uv run --frozen python -m cardinality_lab.prometheus '{{expression}}'

experiment repetitions="3" points="200": build
    uv run --frozen python -m cardinality_lab.experiment --repetitions '{{repetitions}}' --points '{{points}}'

benchmark repetitions="3": build
    uv run --frozen python -m cardinality_lab.benchmark --repetitions '{{repetitions}}'

benchmark-resume repetitions="3": build
    uv run --frozen python -m cardinality_lab.benchmark --repetitions '{{repetitions}}' --resume

benchmark-metrics repetitions="3": build
    uv run --frozen python -m cardinality_lab.benchmark --suites metrics --repetitions '{{repetitions}}'

benchmark-epochs repetitions="3": build
    uv run --frozen python -m cardinality_lab.benchmark --suites epochs --repetitions '{{repetitions}}'

benchmark-scale repetitions="3": build
    uv run --frozen python -m cardinality_lab.benchmark --suites scale --repetitions '{{repetitions}}'

benchmark-scale-large repetitions="1": build
    uv run --frozen python -m cardinality_lab.benchmark --suites scale --large-scale --repetitions '{{repetitions}}'

benchmark-offenders repetitions="3": build
    uv run --frozen python -m cardinality_lab.benchmark --suites offenders --repetitions '{{repetitions}}'

benchmark-histograms repetitions="3": build
    uv run --frozen python -m cardinality_lab.benchmark --suites histograms --repetitions '{{repetitions}}'

render directory="results/benchmark":
    uv run --frozen python -m cardinality_lab.benchmark_report '{{directory}}'
