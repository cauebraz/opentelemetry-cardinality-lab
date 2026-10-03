# Cardinality Guardian benchmark

- Initial collection started at: 2026-10-03T01:56:02.503703+00:00
- Last resumed at: 2026-10-03T10:28:51.258955+00:00
- Platform: macOS-27.0-arm64-arm-64bit-Mach-O
- Versions: collector 0.162.0, otel_proto 1.45.0, prometheus 3.15.0, python 3.14.8
- Modes: baseline, tag_only, overflow_attribute, strip_and_reaggregate
- Repetitions per case: 3
- Threshold: 20 new values per 10s epoch
- Suites: metrics, epochs, scale, offenders
- Runs recorded: 84

Cells show the median across repetitions, with the min-max range in parentheses when repetitions disagreed.

## Correctness

The correctness suite is separate from this benchmark and is not re-run here. It asserts the exact series, label and overflow-marker counts the processor must produce, and it fails the run on any mismatch.

- Source: `results/REPORT.md`, collected 2026-10-03T04:25:37.207860+00:00
- Runs: 9 across modes tag_only, overflow_attribute, strip_and_reaggregate
- Assertion failures: none

## Metric compatibility

Measured fact: identity series present in Prometheus for each OTLP metric shape, from 200 unique input attribute sets per shape.

| metric shape | baseline | tag_only | overflow_attribute | strip_and_reaggregate |
| --- | --- | --- | --- | --- |
| cumulative_exponential_histogram (classic histogram) | 200 | 200 | 200 | 200 |
| cumulative_histogram (classic histogram) | 200 | 200 | 200 | 200 |
| cumulative_sum (counter) | 200 | 200 | 200 | 200 |
| delta_exponential_histogram (classic histogram) | 200 | 200 | 200 | 200 |
| delta_histogram (classic histogram) | 200 | 200 | 200 | 200 |
| delta_sum (counter, reaggregation supported) | 200 | 200 | 24 | 24 |
| gauge (gauge, reaggregation supported) | 200 | 200 | 24 | 24 |
| summary (summary) | 200 | 200 | 200 | 200 |

Complete Prometheus family series, including buckets and sums:

| metric shape | baseline | tag_only | overflow_attribute | strip_and_reaggregate |
| --- | --- | --- | --- | --- |
| cumulative_exponential_histogram | 600 | 600 | 600 | 600 |
| cumulative_histogram | 2200 | 2200 | 2200 | 2200 |
| cumulative_sum | 200 | 200 | 200 | 200 |
| delta_exponential_histogram | 600 | 600 | 600 | 600 |
| delta_histogram | 2200 | 2200 | 2200 | 2200 |
| delta_sum | 200 | 200 | 24 | 24 |
| gauge | 200 | 200 | 24 | 24 |
| summary | 1000 | 1000 | 1000 | 1000 |

Interpretation: upstream reaggregates only Delta Sum and Gauge. Every other shape falls back to tagging, so its identity count under an enforcing mode stays at the baseline value.

## Epoch behavior

Measured fact: identity series after the workload settles, for a burst inside one epoch and for sustained creation across epochs.

| workload | baseline | tag_only | overflow_attribute | strip_and_reaggregate |
| --- | --- | --- | --- | --- |
| burst-single-epoch (2000 unique) | 2000 | 2000 | 24 | 24 |
| sustained-across-epochs (600 unique) | 600 | 600 | 424 | 424 |

Identity series observed at each phase boundary of the sustained workload. These are sampled as the next phase starts, before the last scrape of the phase has settled, so the final column reads below the settled total above.

| mode | epoch-1 | epoch-2 | epoch-3 |
| --- | --- | --- | --- |
| baseline | 200 | 400 | 550 |
| tag_only | 200 | 400 | 550 |
| overflow_attribute | 24 | 224 | 374 |
| strip_and_reaggregate | 24 | 224 | 374 |

Interpretation: enforcement compares each attribute key's current-epoch cardinality estimate with its previous-epoch estimate. The sustained workload replaces 200 identities with 200 different identities each epoch, so epochs two and three show no cardinality growth even though the identities churn completely. They pass through instead of receiving a fresh 20-value allowance.

## Scale

Measured fact: identity series retained as unique attribute values grow, for a Delta Sum sent in one burst.

| unique input values | baseline | tag_only | overflow_attribute | strip_and_reaggregate |
| --- | --- | --- | --- | --- |
| 100 | 100 | 100 | 24 | 24 |
| 1000 | 1000 | 1000 | 24 | 24 |
| 10000 | 10000 | 10000 | 24 | 24 |

Reduction against the unique input values:

| unique input values | baseline | tag_only | overflow_attribute | strip_and_reaggregate |
| --- | --- | --- | --- | --- |
| 100 | 0.0% | 0.0% | 76.0% | 76.0% |
| 1000 | 0.0% | 0.0% | 97.6% | 97.6% |
| 10000 | 0.0% | 0.0% | 99.8% | 99.8% |

Interpretation: these bursts occur in the first epoch, when the previous estimate is zero. Enforcement therefore holds each tracked attribute near the configured growth threshold, plus distinct overflow identities created by bounded labels, while reduction rises with input cardinality.

## Multiple offenders

Measured fact: one metric carrying four correlated unbounded attributes, 1000 unique combinations.

| measurement | baseline | tag_only | overflow_attribute | strip_and_reaggregate |
| --- | --- | --- | --- | --- |
| identity series | 1000 | 1000 | 40 | 40 |
| overflow-marked series | 0 | 980 | 0 | 0 |
| overflow sentinel series | 0 | 0 | 20 | 0 |

Distinct values retained per unbounded attribute:

| attribute | baseline | tag_only | overflow_attribute | strip_and_reaggregate |
| --- | --- | --- | --- | --- |
| customer_id | 1000 | 1000 | 21 | 20 |
| http_route | 1000 | 1000 | 21 | 20 |
| request_id | 1000 | 1000 | 21 | 20 |
| user_id | 1000 | 1000 | 21 | 20 |

Interpretation: the processor tracks each attribute key independently within a metric. Because the four unbounded attributes are perfectly correlated, their estimates cross the threshold at nearly the same point. The remaining bounded method and status labels then produce multiple reaggregated identities.

## Workload execution

Measured fact: how fast the generator actually sent each case and how long Prometheus took to settle. These describe the harness, not the processor.

| case | mode | datapoints/s | send s | settle s | lag s |
| --- | --- | --- | --- | --- | --- |
| burst-single-epoch | baseline | 70067.0 (63697.8-74368.3) | 0.03 (0.03-0.03) | 9.08 (9.08-9.08) | 0.000 |
| burst-single-epoch | tag_only | 74360.6 (64026.6-77958.1) | 0.03 (0.03-0.03) | 9.07 (8.07-9.07) | 0.000 |
| burst-single-epoch | overflow_attribute | 60244.4 (51005.6-65806.1) | 0.03 (0.03-0.04) | 9.06 (9.06-9.07) | 0.000 |
| burst-single-epoch | strip_and_reaggregate | 60543.5 (56513.2-73141.0) | 0.03 (0.03-0.04) | 9.07 (8.06-9.07) | 0.000 |
| sustained-across-epochs | baseline | 21.8 | 27.51 (27.51-27.51) | 4.04 (4.04-4.04) | 0.005 |
| sustained-across-epochs | tag_only | 21.8 | 27.52 (27.52-27.52) | 5.04 (5.04-5.04) | 0.005 |
| sustained-across-epochs | overflow_attribute | 21.8 | 27.52 | 4.03 (4.03-5.05) | 0.005 |
| sustained-across-epochs | strip_and_reaggregate | 21.8 | 27.52 (27.52-27.53) | 4.06 (4.04-5.05) | 0.010 (0.009-0.010) |
| metric-compatibility | baseline | 63632.7 (58537.4-72471.0) | 0.03 (0.02-0.03) | 9.26 (8.25-9.29) | 0.000 |
| metric-compatibility | tag_only | 61480.5 (57835.0-62785.2) | 0.03 (0.03-0.03) | 9.26 (8.27-9.26) | 0.000 |
| metric-compatibility | overflow_attribute | 64700.7 (59987.3-65040.5) | 0.03 (0.03-0.03) | 8.25 (8.25-8.27) | 0.000 |
| metric-compatibility | strip_and_reaggregate | 61549.5 (61091.8-73522.4) | 0.03 (0.02-0.03) | 9.25 (8.23-9.27) | 0.000 |
| multiple-offenders | baseline | 30837.5 (30779.0-31713.9) | 0.03 | 9.10 (8.06-9.13) | 0.000 |
| multiple-offenders | tag_only | 34094.0 (32610.8-34479.5) | 0.03 (0.03-0.03) | 9.08 (8.07-9.09) | 0.000 |
| multiple-offenders | overflow_attribute | 33447.6 (31780.7-35507.2) | 0.03 (0.03-0.03) | 8.09 (8.08-9.10) | 0.000 |
| multiple-offenders | strip_and_reaggregate | 36085.5 (34107.1-38898.5) | 0.03 (0.03-0.03) | 9.07 (8.07-9.10) | 0.000 |
| scale-100 | baseline | 10384.5 (9893.7-11319.2) | 0.01 (0.01-0.01) | 9.09 (9.07-9.11) | 0.000 |
| scale-100 | tag_only | 10220.3 (9845.5-10729.6) | 0.01 (0.01-0.01) | 9.09 (8.09-9.10) | 0.000 |
| scale-100 | overflow_attribute | 9673.8 (9195.0-9936.3) | 0.01 (0.01-0.01) | 9.09 (8.09-9.09) | 0.000 |
| scale-100 | strip_and_reaggregate | 9607.5 (9043.5-11749.3) | 0.01 (0.01-0.01) | 8.09 (8.09-9.11) | 0.000 |
| scale-1000 | baseline | 46010.8 (44829.1-47018.4) | 0.02 (0.02-0.02) | 9.09 (8.09-9.11) | 0.000 |
| scale-1000 | tag_only | 45044.5 (43986.8-50177.5) | 0.02 (0.02-0.02) | 9.10 (9.09-9.10) | 0.000 |
| scale-1000 | overflow_attribute | 45202.6 (42609.9-64820.5) | 0.02 (0.01-0.02) | 9.10 (8.08-9.11) | 0.000 |
| scale-1000 | strip_and_reaggregate | 42541.2 (42101.1-43239.1) | 0.02 (0.02-0.02) | 9.08 (8.09-9.09) | 0.000 |
| scale-10000 | baseline | 108952.4 (104871.4-109288.3) | 0.09 (0.09-0.10) | 9.13 (9.10-9.15) | 0.076 (0.076-0.080) |
| scale-10000 | tag_only | 98186.1 (96686.2-104623.9) | 0.10 (0.10-0.10) | 8.13 (8.10-9.14) | 0.082 (0.077-0.085) |
| scale-10000 | overflow_attribute | 93345.5 (92786.3-101932.3) | 0.11 (0.10-0.11) | 9.09 (8.06-9.10) | 0.087 (0.079-0.088) |
| scale-10000 | strip_and_reaggregate | 96190.9 (89267.6-134514.2) | 0.10 (0.07-0.11) | 9.10 (8.09-12.12) | 0.084 (0.057-0.092) |

## Limitations

- Single-host Docker Compose on one machine. Numbers are not a capacity model for a production Collector.
- Docker CPU and memory samples are diagnostic context only. The harness and the Collector share the same host, so they do not isolate processor overhead.
- Cardinality estimation upstream is HyperLogLog++ with about 0.81% standard error, so retained counts near the threshold vary between runs.
- The Collector's Prometheus exporter renders OTLP exponential histograms as classic bucketed histograms, so this report cannot say anything about native-histogram handling.
- Each run starts a fresh stack, so results exclude warm-cache and long-running drift effects.
- One threshold and one epoch duration per run; the report does not sweep them.
- Collector telemetry did not expose these counters: `otelcol_exporter_send_failed_metric_points`, `otelcol_processor_incoming_items`, `otelcol_processor_outgoing_items`. `results.json` records them as `null`, not zero.
