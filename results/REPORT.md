# OpenTelemetry Cardinality Guardian experiment

- Collected at: 2026-10-03T04:25:37.207860+00:00
- Collector: 0.162.0
- Prometheus: 3.15.0
- Python OpenTelemetry SDK: 1.45.0
- Points per scenario: 200
- Repetitions: 3
- Cardinality threshold: 20 new values per epoch
- Epoch: 10 seconds

## Result

| Mode | Stable | User ID | Unbounded route | Histogram |
| --- | ---: | ---: | ---: | ---: |
| tag_only | 4 | 200 | 200 | 200 |
| overflow_attribute | 4 | 21 | 21 | 200 |
| strip_and_reaggregate | 4 | 21 | 21 | 200 |

## Label behavior

| Mode | Scenario | Dynamic-label series | Overflow-marked series |
| --- | --- | ---: | ---: |
| tag_only | user-id | 200 | 180 |
| tag_only | unbounded-route | 200 | 180 |
| tag_only | histogram | 200 | 180 |
| overflow_attribute | user-id | 21 | 0 |
| overflow_attribute | unbounded-route | 21 | 0 |
| overflow_attribute | histogram | 200 | 180 |
| strip_and_reaggregate | user-id | 20 | 0 |
| strip_and_reaggregate | unbounded-route | 20 | 0 |
| strip_and_reaggregate | histogram | 200 | 180 |

Dynamic-label series retain the scenario's unsafe label. Overflow-
marked series carry `otel.metric.overflow=true` after translation.
Replacement values are retained in `results.json`.

Each value is the median active Prometheus series. A parenthesized
range appears when repetitions differ.

## Assertions

- Stable labels remain at 4 series in every mode.
- `tag_only` retains all 200 dynamic-label series.
- Delta counters in `overflow_attribute` and `strip_and_reaggregate` converge to 21 series: the values observed before the threshold plus one reaggregated overflow series.
- Histograms retain all 200 series in every mode because the alpha processor falls back to `tag_only` when safe spatial reaggregation is unavailable.

## Collector self-telemetry

- `otelcol_processor_cardinality_labels.stripped`
- `otelcol_processor_cardinality_savings.estimated`
- `otelcol_processor_cardinality_top.offenders`
- `otelcol_processor_cardinality_trackers.active`
- `otelcol_processor_cardinality_trackers.rejected`

## Reproduce

```bash
just setup
just check
just experiment
```

The experiment uses local containers and sends no telemetry externally.
Results are measurements from this run, not vendor pricing estimates.
