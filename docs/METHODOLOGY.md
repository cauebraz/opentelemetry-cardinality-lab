# Methodology

- Designed: 2026-10-02
- Environment: local Docker containers
- External data: none
- Primary output: active Prometheus series per source metric

## Independent variable

The Cardinality Guardian `enforcement_mode`:

1. `tag_only`
2. `overflow_attribute`
3. `strip_and_reaggregate`

The collector restarts with an empty Prometheus volume before every repetition.
This prevents prior runs from contributing historical series.

## Controlled inputs

- Collector version: `0.162.0`
- Prometheus version: `3.15.0`
- OpenTelemetry Python SDK: `1.45.0`
- Data points per scenario: 200
- Cardinality threshold: 20 new values per epoch
- Epoch duration: 10 seconds
- Repetitions: 3
- OTLP temporality preference: delta

Versions are release pins resolved on 2026-10-02. The remaining numbers are
experiment parameters chosen on 2026-10-02 to trigger the processor without
requiring production traffic.

## Dependent variables

- Number of active Prometheus series for the scenario metric.
- Number of series retaining the unsafe dynamic label.
- Number of series carrying `otel.metric.overflow=true`.
- Overflow replacement values exposed after Prometheus translation.
- Cardinality Guardian self-telemetry exposed by the collector.
- Point-in-time collector container CPU and memory statistics.
- Emission and total run duration.

Only active series participate in pass or fail assertions. Container statistics
are retained as context because one snapshot does not establish performance.

## Hypotheses

### H1: stable labels are unchanged

Four fixed `http.route` values produce four active series in every mode.

### H2: tag-only detects without protecting

Two hundred unique values produce 200 series because the original dynamic label
remains attached. The 180 values above the configured threshold carry
`otel.metric.overflow=true`.

### H3: supported enforcement converges

For a delta counter, the first 20 values remain distinct. Values after the
threshold collapse into one reaggregated identity, producing 21 series.
`overflow_attribute` retains 21 label values, including
`otel.cardinality_overflow`. `strip_and_reaggregate` retains the first 20 label
values and exposes the reaggregated series without the unsafe label.

### H4: histograms fall back safely

The processor does not reaggregate histograms. Two hundred unique values remain
200 series and the 180 values above the threshold receive the tag-only marker.

## Failure interpretation

An assertion failure means one of the following:

- upstream behavior changed;
- the exporter changed metric identity;
- the experiment did not wait for a complete scrape;
- the hypothesis misunderstood the processor.

The raw JSON, collector logs, exact release pins, and repeated runs are retained
so the cause can be distinguished before changing an assertion.

# Benchmark methodology

The benchmark answers a different question from the correctness suite. The
correctness suite asserts what the processor must do. The benchmark measures
how much the processor changes, across metric shapes, epochs and input
cardinality, against a Collector that does not run the processor at all.

The two never share a result file. The correctness suite writes
`results/results.json`; the benchmark writes `results/benchmark/results.json`
with a `results_version` field.

## Baseline

`config/collector-baseline.yaml` is the same receiver, batcher and exporter
pipeline with the `cardinality_guardian` processor removed. `baseline` is a
fourth mode beside the three enforcement modes, so every number has an
unprocessed reference measured on the same stack.

`compose.yaml` selects the config through `COLLECTOR_CONFIG`, and the
threshold and epoch duration through `CARDINALITY_THRESHOLD` and
`EPOCH_SECONDS`. Their defaults reproduce the correctness suite exactly.

## Workload generation

The correctness suite uses the OpenTelemetry Python SDK. The SDK has no
Summary and no Exponential Histogram instrument, so the benchmark encodes OTLP
protobuf directly (`cardinality_lab/otlp.py`) and sends it over gRPC. Unit
tests assert temporality, timestamps, attributes, bucket placement, scales and
quantiles before any container starts.

Two workloads (`cardinality_lab/workloads.py`):

| Workload | Unbounded attributes | Bounded attributes |
| --- | --- | --- |
| `single-offender` | `user.id` | `environment`, `region`, `http.method` |
| `multiple-offenders` | `user.id`, `request.id`, `http.route`, `customer.id` | `environment`, `region`, `http.method`, `http.status_code` |

`multiple-offenders` correlates its four unbounded attributes: index `i`
produces exactly one combination, not a Cartesian product.

## Suites

| Suite | Cases | Metric shapes |
| --- | --- | --- |
| `metrics` | 200 unique values, every OTLP shape sent together | all eight |
| `epochs` | 2,000-value burst in one epoch; 200 new values per epoch across three epochs | Delta Sum |
| `scale` | 100, 1,000 and 10,000 unique values; 100,000 behind `--large-scale` | Delta Sum |
| `offenders` | 1,000 unique four-attribute combinations | Delta Sum |

The processor maintains one cardinality tracker per `(metric name, attribute
key)`. At each epoch it compares that key's current cardinality estimate with
its previous-epoch estimate. The threshold therefore limits estimated
cardinality growth, not identity churn: replacing 200 values with 200 different
values can produce a delta near zero. The multiple-offender case uses
correlated attributes so their independent trackers receive new values at the
same rate.

## Dependent variables

- `input_identity_series`: unique attribute sets generated for the metric.
- `output_identity_series`: series in the metric's representative Prometheus
  family (`_count` for histograms and summaries, `_total` for counters, the
  bare name for gauges).
- `reduction`: `1 - output_identity_series / input_identity_series`.
- `total_family_series`: every Prometheus series the metric produces,
  including buckets, sums and quantiles.
- Distinct retained values per unbounded attribute, overflow-marked series and
  overflow-sentinel series.
- Collector counters when exposed by the selected Collector telemetry level:
  receiver accepted and refused points, exporter sent and failed points, and
  processor incoming and outgoing items. A missing metric is recorded as
  unavailable, not as zero.
- Send duration, achieved datapoints per second, schedule lag, settle time.
- Docker CPU and memory samples, recorded as diagnostic context only.

## Measurement barriers

The benchmark does not sleep a fixed interval and assume the data arrived. It
polls Prometheus until the series counts for every family of the case are
unchanged across three consecutive scrapes (`wait_until_stable`). A case that
never settles raises instead of reporting a number.

Every run carries a `validity` object. It fails when the datapoints sent do
not match the plan, OTLP rejects a datapoint, receiver accepted/refused
counters disagree with delivery, exporter counters report an impossible or
failed delivery, or the sender falls more than one second behind a timed
schedule. A scheduled workload that the harness could not keep up with is
reported as invalid rather than published as a rate measurement.

## Statistics

Each case runs three times per mode. The report shows the median, and the
min-max range whenever repetitions disagreed. Raw per-repetition samples stay
in `results.json` under `summary[].*.samples`.

## What the benchmark does not measure

- Processor CPU and memory overhead. The generator, the Collector and
  Prometheus share one host, so the Docker samples cannot be attributed. The
  upstream repository already carries Go microbenchmarks for CPU cost.
- Throughput capacity. The reported rate is what the harness achieved, not
  what the Collector can absorb.
- Behavior over hours, across Collector restarts, or under memory pressure.
