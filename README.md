# OpenTelemetry Cardinality Lab

A local, reproducible experiment for the alpha OpenTelemetry Cardinality
Guardian processor. The lab answers one question:

> What reaches Prometheus when a metric label starts producing unbounded
> values under each enforcement mode?

The lab does not propose another collector or cardinality product. It exercises
the upstream processor as released, records the resulting active series, and
turns its documented edge cases into regression assertions.

It has three halves that never share a result file:

| Half | Command | Question | Output |
| --- | --- | --- | --- |
| Correctness | `just experiment` | Does the processor do exactly what it must? Fails the run on any mismatch. | `results/` |
| Benchmark | `just benchmark` | How much does it change, per metric shape, per epoch and per input cardinality, against a Collector without the processor? | `results/benchmark/` |
| Histogram reduction | `just benchmark-histograms` | The processor leaves histograms untouched. What does reduce them, and which question does each option cost? | `results/histograms/` |

## Why this exists

The processor has three modes with different operational consequences:

- `tag_only` preserves every original label and adds
  `otel.metric.overflow=true`. It detects an explosion but does not stop it.
- `overflow_attribute` replaces the offending value and reaggregates metric
  types for which spatial aggregation is safe.
- `strip_and_reaggregate` removes the offending label and reaggregates metric
  types for which spatial aggregation is safe.

The upstream documentation states that delta sums and gauges support
reaggregation, while cumulative sums, histograms, exponential histograms, and
summaries fall back to `tag_only`. Source: OpenTelemetry Collector Contrib
Cardinality Guardian README at tag `v0.162.0`.

## Finding

In the local run, both enforcing modes reduced each modeled delta-counter
explosion from 200 active series to 21, an 89.5% reduction. The same modes left
the histogram at 200 active series. All three repetitions produced the same
counts. Source: [`results/results.json`](results/results.json).

Of the options that do reduce a histogram, the one that reduced series without
losing a question kept every identity: exponential buckets scraped as native
histograms held the same 200 identities in 200 series instead of 2,200. Every
option that reduced identities cost the per-user percentile. Source:
[`results/histograms/results.json`](results/histograms/results.json).

## Experiment

The default run sends 200 data points per scenario, repeats every mode three
times, and configures a threshold of 20 new values per 10-second epoch. Those
numbers are experiment choices, not production recommendations.

| Scenario | Metric | Label behavior | Question |
| --- | --- | --- | --- |
| Stable | Delta counter | Four fixed routes | Does normal traffic remain unchanged? |
| User ID | Delta counter | One unique `user.id` per point | Do enforcement modes collapse an unsafe identity label? |
| Unbounded route | Delta counter | One concrete order path per point | Does a raw URL path behave like an identity label? |
| Histogram | Delta histogram | One unique `user.id` per point | Does the documented fallback preserve every series? |

The generated [report](results/REPORT.md) contains the measurements, and
[`results.json`](results/results.json) contains the machine-readable evidence.

## Benchmark

The benchmark adds a fourth mode, `baseline`, running
[`config/collector-baseline.yaml`](config/collector-baseline.yaml): the same
pipeline with the processor removed. Every number therefore has an unprocessed
reference measured on the same stack.

Because the OpenTelemetry Python SDK has no Summary instrument and reaches an
exponential histogram only through a view, the benchmark encodes OTLP protobuf
directly ([`cardinality_lab/otlp.py`](cardinality_lab/otlp.py)) so all eight
metric shapes can be compared.

| Suite | Command | What it varies |
| --- | --- | --- |
| Metric compatibility | `just benchmark-metrics` | All eight OTLP metric shapes at 200 unique values |
| Epoch behavior | `just benchmark-epochs` | A 2,000-value burst in one epoch against 200 new values per epoch over three epochs |
| Scale | `just benchmark-scale` | 100, 1,000 and 10,000 unique values |
| Multiple offenders | `just benchmark-offenders` | Four correlated unbounded attributes on one metric |

```bash
just benchmark              # every suite, three repetitions
just benchmark-resume       # finish an interrupted run, keeping recorded runs
just benchmark-scale-large  # adds the 100,000-value case, opt-in
```

The full run takes a couple of hours because every repetition starts a fresh
stack. `just benchmark-resume` reuses the runs already in
`results/benchmark/results.json` and only executes the missing ones. It
refuses to resume when the version, platform, suites, modes, case definitions,
threshold, epoch duration or repetition count differ from the recorded
artifact.

The 100,000-value case is opt-in because it is slow and adds no behavior the
10,000-value case does not already show.

Results land in [`results/benchmark/REPORT.md`](results/benchmark/REPORT.md),
which separates measured facts from interpretation and limitations, and in
`results/benchmark/results.json`, which carries a `results_version`, the raw
per-repetition samples and a `validity` object per run.

Methodology, dependent variables and measurement barriers:
[`docs/METHODOLOGY.md`](docs/METHODOLOGY.md).

## Histogram reduction

The benchmark above shows the processor leaving histograms at their input
series count. This suite asks what does reduce them. It replaces the
enforcement mode with a strategy: a Collector config, a Prometheus config, an
emitter, and whether the pipeline is expected to export anything at all,
selected together by name in
[`cardinality_lab/stack.py`](cardinality_lab/stack.py).

| Strategy | What it does |
| --- | --- |
| `baseline` | The pipeline without the processor |
| `strip_and_reaggregate` | The Cardinality Guardian, for reference |
| `transform-delete` | `transform` deletes the unbounded attribute key |
| `transform-aggregate` | `transform` aggregates on the remaining attributes |
| `filter` | `filter` drops every datapoint carrying the attribute |
| `sdk-baseline` | The Python SDK with every attribute, as an emitter reference |
| `sdk-view` | A Python SDK view limits the instrument's attribute keys |
| `routing` | `tag_only` plus the `routing` connector and a second exporter |
| `routing-string` | The same routing table with the marker read as a string |
| `exponential` | Exponential buckets, scraped as text |
| `exponential-native` | Exponential buckets, scraped as native histograms |

Each strategy is measured twice over: the Prometheus series it produces, and
four fixed PromQL queries that stand for what an operator asks of a histogram.
A query that returns nothing is recorded as `null`, which is the price of the
reduction in the same row as its benefit. The Collector log of each run is
counted as well: lines at `warn` level or above, and lines carrying
`Dropped misaligned histogram datapoint`, so a strategy that changes a count
while logging the drop is told apart from one that does it silently.

```bash
just benchmark-histograms          # three repetitions, about nine minutes
just render results/histograms     # re-render the report from the recorded run
```

The suite runs alone and writes
[`results/histograms/REPORT.md`](results/histograms/REPORT.md), so the
benchmark artifacts above stay as they were published.

## Run

Requirements:

- Docker with Compose
- `uv`
- `just`

```bash
just setup
just check
just experiment
```

Use a different number of repetitions or points:

```bash
just experiment 5 500
```

The experiment binds only to localhost:

- OTLP gRPC: `127.0.0.1:14317`
- Collector self-telemetry: `127.0.0.1:18888`
- Exported metrics: `127.0.0.1:19464`
- Exported metrics, second exporter in the routing strategies:
  `127.0.0.1:19465`
- Collector health: `127.0.0.1:13133`
- Prometheus: `127.0.0.1:19090`

Run one mode manually:

```bash
just up overflow_attribute
just emit user-id
just query 'count(lab_user_id_requests_total)'
just down
```

## Components

- OpenTelemetry Collector Contrib `0.162.0`, downloaded from its GitHub release
  and verified against the release SHA-256 for `linux/amd64` or `linux/arm64`.
- Prometheus `3.15.0`, pinned to its multi-architecture image digest.
- OpenTelemetry Python SDK and OTLP exporter `1.45.0`.
- Python `3.14.8`.

The versions were resolved from stable upstream releases. Every Python
dependency is exact in `pyproject.toml` and resolved transitively in `uv.lock`.

## Boundaries

- The lab creates no cloud resources and sends no data outside the local
  Docker network.
- The Prometheus series count measures exported identity, not a vendor bill.
- The collector CPU and memory fields in both result files are raw Docker
  samples taken on the same host as the generator. They are diagnostic
  context, not an overhead measurement. Upstream already carries Go
  microbenchmarks for processor CPU cost.
- The benchmark reports the rate the harness achieved, not the rate the
  Collector can absorb.
- The Collector's Prometheus exporter converts OTLP exponential histograms to
  native histograms, and a scrape job reads them as native only with
  `scrape_native_histograms: true`. Only the `exponential-native` strategy
  sets it, so every other run that carries an exponential histogram sees a
  single `+Inf` bucket. Classic histograms keep their explicit buckets in
  every run.
- HyperLogLog++ is probabilistic. Repetitions expose variation rather than
  treating one run as universal evidence.
- The configured threshold is deliberately small to keep the experiment fast.

## Report a mismatch

When a run against your own workload produces numbers that differ from the
tables here or in the articles, open an issue in this repository with the
enforcement mode or strategy, the scenario or benchmark suite, the Collector
version, and the `results.json` the run produced, from `results/`,
`results/benchmark/` or `results/histograms/`. A collector log from `results/` helps when the processor was
expected to enforce and did not.

## Source material

- [Cardinality Guardian processor at `v0.162.0`](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/processor/cardinalityguardianprocessor)
- [Collector internal telemetry](https://opentelemetry.io/docs/collector/internal-telemetry/)
- [Prometheus exporter at `v0.162.0`](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/exporter/prometheusexporter)
- [Transform processor at `v0.162.0`](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/processor/transformprocessor)
- [Filter processor at `v0.162.0`](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/processor/filterprocessor)
- [Routing connector at `v0.162.0`](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/ae8c507510f48f433ab47dd1c6b01a59d6c388b5/connector/routingconnector)
- [Prometheus native histograms](https://prometheus.io/docs/specs/native_histograms/)

## License

Copyright 2026 Caue Braz.

Licensed under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for
project attribution.
