# Histogram reduction benchmark

- Platform: macOS-27.0.1-arm64-arm-64bit-Mach-O
- Versions: collector 0.162.0, prometheus 3.15.0, otel_proto 1.45.0, python 3.14.8
- Strategies: baseline, strip_and_reaggregate, transform-delete, transform-aggregate, filter, sdk-baseline, sdk-view, routing, routing-string, exponential, exponential-native
- Repetitions per case: 3
- Threshold: 20 new values per 10s epoch
- Suites: histograms
- Runs recorded: 33

Cells show the median across repetitions, with the min-max range in parentheses when repetitions disagreed.

## Histogram reduction

Measured fact: Prometheus series and answerable queries for one delta histogram carrying 200 unique attribute sets, under each strategy. A `null` cell means the query returned nothing.

| strategy | representation | identity series | total series | total_requests | p95_by_method | requests_by_status | p95_for_one_user |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | classic | 200 | 2200 | 600 | 9.107 | 120 | 4.750 |
| strip_and_reaggregate | classic | 200 | 2200 | 600 | 9.107 | 120 | 4.750 |
| transform-delete | classic | 20 | 220 | 60 | 9.375 | 12 | null |
| transform-aggregate | classic | 20 | 220 | 600 | 9.107 | 120 | null |
| filter | absent | 0 | 0 | null | null | null | null |
| sdk-baseline | classic | 200 | 2200 | 600 | 9.107 | 120 | 4.750 |
| sdk-view | classic | 20 | 220 | 600 | 9.107 | 120 | null |
| routing | classic | 200 | 2200 | 600 | 9.107 | 120 | 4.750 |
| routing-string | classic | 200 | 2200 | 600 | 9.107 | 120 | 4.750 |
| exponential | classic | 200 | 600 | 600 | null | 120 | null |
| exponential-native | native | 200 | 200 | 600 | 6.999 | 120 | 1.000 |

Series per Prometheus scrape job, for the strategies that write to more than one exporter:

| strategy | lab-metrics | lab-overflow |
| --- | --- | --- |
| routing | 220 | 1980 |
| routing-string | 2200 | 0 |

`routing-string` routed nothing and reported no error. The processor writes `otel.metric.overflow` as a boolean, and Prometheus renders that boolean as the label value `true`, so a condition written from the exported metric compares a boolean with a string and never matches.

Collector log lines at `warn` level or above, per run, for the strategies that wrote any:

| strategy | warn lines | error lines | `Dropped misaligned histogram datapoint` lines |
| --- | --- | --- | --- |
| transform-delete | 22 | 0 | 11 |

`baseline`, `strip_and_reaggregate`, `transform-aggregate`, `filter`, `sdk-baseline`, `sdk-view`, `routing`, `routing-string`, `exponential`, `exponential-native` wrote no line at `warn` level or above. The Collector samples repeated log lines (10 per 10 s tick, then every 100th by default), so a line count is a floor on the events it reports, not the number of datapoints dropped.

Interpretation: `total_requests` is an integrity check rather than a question. A strategy that changes it did not trade a question for series, it changed the answer. `p95_for_one_user` is the question every reducing strategy is expected to lose.

## Workload execution

Measured fact: how fast the generator actually sent each case and how long Prometheus took to settle. These describe the harness, not the processor.

| case | mode | datapoints/s | send s | settle s | lag s |
| --- | --- | --- | --- | --- | --- |
| histogram-reduction | baseline | 22222.2 (14285.7-28571.4) | 0.01 (0.01-0.01) | 8.62 (8.62-9.11) | 0.000 |
| histogram-reduction | strip_and_reaggregate | 22222.2 (22222.2-28571.4) | 0.01 (0.01-0.01) | 8.62 (8.60-9.10) | 0.000 |
| histogram-reduction | transform-delete | 22222.2 (20000.0-25000.0) | 0.01 (0.01-0.01) | 8.62 (8.60-9.12) | 0.000 |
| histogram-reduction | transform-aggregate | 22222.2 (20000.0-25000.0) | 0.01 (0.01-0.01) | 8.60 (8.59-9.07) | 0.000 |
| histogram-reduction | filter | 28571.4 (22222.2-28571.4) | 0.01 (0.01-0.01) | 8.09 (8.08-9.09) | 0.000 |
| histogram-reduction | sdk-baseline | 15384.6 (13333.3-16666.7) | 0.01 (0.01-0.01) | 8.60 (8.59-9.62) | 0.000 |
| histogram-reduction | sdk-view | 2500.0 (2222.2-4000.0) | 0.01 (0.01-0.01) | 9.10 (8.59-9.11) | 0.000 |
| histogram-reduction | routing | 33333.3 (22222.2-33333.3) | 0.01 (0.01-0.01) | 9.10 (8.63-9.11) | 0.000 |
| histogram-reduction | routing-string | 25000.0 (20000.0-40000.0) | 0.01 (0.01-0.01) | 8.60 (8.60-8.60) | 0.000 |
| histogram-reduction | exponential | 18181.8 (16666.7-25000.0) | 0.01 (0.01-0.01) | 9.59 (9.09-9.60) | 0.000 |
| histogram-reduction | exponential-native | 13333.3 (13333.3-14285.7) | 0.01 (0.01-0.01) | 9.09 (8.58-9.10) | 0.000 |

## Limitations

- Single-host Docker Compose on one machine. Numbers are not a capacity model for a production Collector.
- Docker CPU and memory samples are diagnostic context only. The harness and the Collector share the same host, so they do not isolate processor overhead.
- Cardinality estimation upstream is HyperLogLog++ with about 0.81% standard error, so retained counts near the threshold vary between runs.
- The Collector's Prometheus exporter converts OTLP exponential histograms to native histograms, but a scrape job reads them as native only with `scrape_native_histograms: true`. Only the `exponential-native` strategy sets it, so every other exponential histogram arrives as a single `+Inf` bucket. Classic histograms keep their explicit buckets in every run.
- Each run starts a fresh stack, so results exclude warm-cache and long-running drift effects.
- One threshold and one epoch duration per run; the report does not sweep them.
- The histogram values are synthetic and repeat across identities, so the percentile cells show whether a question can be answered, not what a real latency distribution looks like.
- The questions read the buckets directly instead of through `rate()`, because the workload is one burst into a fresh stack and a rate over constant series is zero. The cells describe the whole run, not a window.
- Collector telemetry did not expose these counters: `otelcol_exporter_send_failed_metric_points`, `otelcol_exporter_sent_metric_points`, `otelcol_processor_incoming_items`, `otelcol_processor_outgoing_items`. `results.json` records them as `null`, not zero.
