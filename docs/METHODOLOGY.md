# Methodology

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

Versions are release pins. The remaining numbers are experiment parameters
chosen to trigger the processor without requiring production traffic.

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

`compose.yaml` selects the Collector config through `COLLECTOR_CONFIG`, the
Prometheus config through `PROMETHEUS_CONFIG`, and the threshold and epoch
duration through `CARDINALITY_THRESHOLD` and `EPOCH_SECONDS`. Their defaults
reproduce the correctness suite exactly.

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

# Histogram reduction methodology

The Cardinality Guardian leaves histograms at their input series count. The
`histograms` suite asks what does reduce them, and what each option costs in
questions the data can still answer.

The suite runs alone and writes `results/histograms/`, so the benchmark
artifacts stay as they were published. `just render <directory>` rebuilds a
report from a recorded `results.json` without starting the stack.

## Independent variable

The strategy, not the enforcement mode. A strategy is a Collector config, a
Prometheus config, an emitter, and an `exports` flag saying whether the
pipeline should deliver anything, selected together by name
(`cardinality_lab/stack.py`, `STRATEGIES`).

| Strategy | Collector | Emitter |
| --- | --- | --- |
| `baseline` | no processor | OTLP encoder |
| `strip_and_reaggregate` | Cardinality Guardian | OTLP encoder |
| `transform-delete` | `transform`, `delete_key(datapoint.attributes, "user.id")` | OTLP encoder |
| `transform-aggregate` | `transform`, `aggregate_on_attributes("sum", [...])` | OTLP encoder |
| `filter` | `filter`, drops every datapoint carrying `user.id` | OTLP encoder |
| `sdk-baseline` | no processor | Python SDK, explicit buckets, every attribute |
| `sdk-view` | no processor | Python SDK, explicit buckets, view limits the attribute keys |
| `routing` | Cardinality Guardian in `tag_only` plus the `routing` connector | OTLP encoder |
| `routing-string` | The same routing table, with the marker compared as a string | OTLP encoder |
| `exponential` | no processor | Python SDK, exponential buckets |
| `exponential-native` | no processor, Prometheus scrapes with `scrape_native_histograms: true` | Python SDK, exponential buckets |

`sdk-baseline` exists so that the SDK rows have an SDK reference. Without it a
difference between `baseline` and `sdk-view` could come from the view or from
the emitter.

## Controlled inputs

- Workload: `histogram-user-id`, one delta histogram carrying `environment`,
  `region`, `http.method` (4 values), `http.status_code` (5 values) and an
  unbounded `user.id`.
- Unique attribute sets: 200, in one burst.
- Explicit bounds: the eight bounds in `cardinality_lab/otlp.py`, so a classic
  histogram produces 11 Prometheus series per identity (9 buckets, `_sum`,
  `_count`).
- Three observations per identity, so the count across the metric is 600.
- Exponential aggregation: `max_size` 160, `max_scale` 20, the Python SDK
  defaults. Bucket count changes the size of a native histogram sample, not the
  number of series, so the suite does not sweep it.
- Collector, Prometheus, SDK and threshold as in the benchmark above. When the
  suite was written these pins were also the newest stable releases, resolved
  with `gh api repos/open-telemetry/opentelemetry-collector-releases/releases/latest`,
  the same call against `prometheus/prometheus`, and the PyPI JSON API for
  `opentelemetry-sdk`.

`http.method` and `http.status_code` move with the index, so the 200 identities
cover all 20 method and status pairs.

The native-histogram row reaches Prometheus by scrape, with
`scrape_native_histograms: true` on the job, not through the remote write
exporter. Prometheus 3.x negotiates the protobuf exposition format per scrape
job, which keeps the strategy to a Prometheus config change next to a Collector
config change. The `prometheusremotewrite` path has its own conversion settings
and is not measured here.

## Dependent variables

- `identity_series`: series in the identity family. `_count` for a classic
  histogram. For a native histogram there is no `_count`, so the bare metric
  name carries the identity.
- `total_series`: every series the metric produces across `_bucket`, `_sum`,
  `_count` and the bare name.
- `series_by_job`: the same count split by Prometheus scrape job, so a strategy
  that moves series to a second exporter is not read as a reduction.
- One scalar per question below, or `null` when the query returns nothing.
- `collector_log`: lines at `warn` level, lines at `error` level or above,
  and lines carrying `Dropped misaligned histogram datapoint` in the
  Collector log of the run, so a strategy that changes a count while logging
  the drop is told apart from one that does it silently. The Collector's
  default log sampling (10 lines per 10 s tick, then every 100th) makes the
  count a floor on the events, not the number of datapoints dropped.

## The questions

Four PromQL queries run against every strategy after the series settle. Each
has a classic and a native-histogram form, chosen by which representation the
strategy produced.

| Question | What a `null` means |
| --- | --- |
| `total_requests` | The count is gone or was never exported |
| `p95_by_method` | The distribution can no longer be split by method |
| `requests_by_status` | The status breakdown is gone |
| `p95_for_one_user` | The per-user question is gone, which is the point of most of these strategies |

`total_requests` is the integrity check. A strategy that reports a number other
than 600 did not lose a question, it corrupted the answer, and that is a
different failure.

### How the queries differ from the usual form

The documented shape of a histogram query wraps the buckets in
`rate(...[1m])`. These queries read the buckets directly, because the workload
is one burst into a stack that was created for the run. After the first scrape
the series are constant, so a rate over them is zero and the quantile of a
zero vector is `NaN`. The queries therefore measure the whole run rather than a
window of it, and no cell in this suite is a per-second figure.

`p95_by_method` wraps the per-method vector in `max()` and
`requests_by_status` asks for one status (`500`) instead of a breakdown. Both
reduce a vector to the single scalar a table cell can hold. The breakdown is
still visible through the scalar: the four methods and five status codes move
with the index, so every status carries 120 of the 600 observations, and a
strategy that changes one changes all of them.

The instrument is `lab.bench.delta_histogram`, the delta histogram already
defined in `cardinality_lab/otlp.py`, so both emitters and the benchmark suites
describe the same metric.

## Hypotheses

H5 to H8 and H10 were written before the first run, from the upstream
documentation at `v0.162.0`. The first paragraph of H9 was too. Its second
paragraph, naming `PutBool` and the `routing-string` strategy, was written
after a smoke run showed the string comparison matching nothing; the
hypothesis it replaced expected the string form to work.

### H5: deleting the key reduces series and corrupts the count

`delete_key` runs per datapoint and does not merge datapoints. Two hundred
datapoints arrive at the Prometheus exporter with 20 distinct identities, so
the exporter keeps one value per identity instead of their sum. Expected: 220
series, `total_requests` below 600.

The exporter's rule for a delta histogram is in
`exporter/prometheusexporter/accumulator.go` at `v0.162.0`: a datapoint whose
start timestamp equals the previous point's end timestamp for the same
identity is added to it; one whose start differs and is not later than the
previous end is dropped with a `warn` log, `Dropped misaligned histogram
datapoint`. Points from one burst share start and end, so after `delete_key`
one point per collapsed identity survives and the rest are dropped with that
warning. This paragraph was written after the first run, from the recorded
logs; the `collector_log` field was added to the harness afterwards and the
suite rerun, with the series and question cells unchanged.

### H6: aggregating on the remaining attributes reduces series and keeps the count

`aggregate_on_attributes` runs in the `metric` context and supports histograms
with the `sum` function. Expected: 220 series, `total_requests` 600.

### H7: the filter removes the metric, not the label

Dropping every datapoint that carries `user.id` drops every datapoint of this
metric, and a metric with no datapoints is dropped. Expected: 0 series, every
question `null`.

### H8: a view keeps the series from being created

The SDK aggregates before export, so the Collector never sees 200 identities.
Expected: 220 series and `total_requests` 600, matching H6 at a lower cost to
the Collector. `sdk-baseline` is expected to match `baseline` at 2,200.

### H9: routing moves series, it does not reduce them

`tag_only` marks the datapoints above the threshold and the routing connector
sends the marked ones to a second exporter. Expected: 2,200 series in total,
split between the two scrape jobs.

The processor writes the marker with `attrs.PutBool("otel.metric.overflow",
true)` (`processor.go:472` at `v0.162.0`), while Prometheus renders it as the
label value `"true"`. `routing-string` compares it as a string, which is what
the exported metric suggests. Expected: no match, no error, every series in the
first job.

### H10: the exponential histogram changes the series per identity, not the identities

Scraped as text, a native histogram exposes only its `+Inf` bucket, so 200
identities produce 600 series and the distribution is gone. Scraped with
`scrape_native_histograms: true`, each identity is one series carrying the
whole distribution, so 200 identities produce 200 series and every question,
including `p95_for_one_user`, still answers. Expected: an 11-fold reduction in
series with no reduction in identities.

## Measurement barriers

The `filter` strategy is expected to produce zero series, so this suite waits
for the Collector's receiver counter to move before it waits for the series
counts to settle. Without that order, an empty scrape would be read as a
settled result.

Zero is a settled answer only for `filter`. Every other strategy carries
`exports = True` in its `Strategy` entry, so `wait_until_stable` refuses to
settle on three empty scrapes and raises instead. A batch timeout and a scrape
interval of one second each can delay the first useful scrape, and without the
flag that delay would be published as a metric that never arrived.

Delivery counters are read per component (`receiver="otlp"`,
`exporter=~"prometheus.*"`). A connector is a receiver and an exporter at the
same time, so an unscoped sum would count the routed datapoints twice and
report an impossible delivery.

The SDK aggregates before export, so it cannot report how many datapoints it
sent. Its rate comes from the receiver counter after the run, the same quantity
the OTLP encoder reports directly, so `datapoints/s` means the same thing in
every row. Recorded measurements stay in `results.json` as
`recorded_measurements`.

## Outcome

Three repetitions, every repetition in agreement. H5 through H10 held as
written. H5 held with one detail the hypothesis did not predict: the exporter
logs the drop. Each `transform-delete` run in `results/histograms/logs/`
carries 11 `Dropped misaligned histogram datapoint` lines at `warn` level,
the Collector's log sampler reducing 180 drops to that count, and no other
strategy logs a warning. The harness counts these lines per run as
`collector_log`, and the report lists the strategies that wrote any. H9 held
only after a correction: the first routing table compared the
marker with the string `"true"`, matched nothing and logged nothing. That
configuration is kept as the `routing-string` strategy, because the condition a
reader would write from the exported metric is the one that silently does not
work.
