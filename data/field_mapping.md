# Organizer generator field mapping

Source: the three untouched outputs in `data/generator_raw/`.

Each file contains one observation for one device. The generator supplies no
regular sampling interval. None of these files contains a JSON null. In the
table, “if missing/null” describes how the adapter should handle that case;
it is not a claim that the generator produced null.

| Raw key | Example value | Unit | Interval | If missing/null | Canonical field | Conversion | Decision and reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `timestamp` | `2026-10-31T17:22:28.014797Z` | UTC time | One per call | Flag invalid timestamp; do not invent one | `observed_at` | Parse ISO 8601 UTC | Map as observation metadata |
| `device_id` | `SW_CAMPUS_2` | Device ID | One per call | Flag missing target ID | `device_id` | Copy unchanged | Map as observation target |
| `metrics` | JSON object | None | One per call | Flag missing metrics; do not create zero values | None | Read its child keys | Container only; not a metric observation |
| `metrics.cpu_utilization_pct` | `11.13` | Percent, inferred from key | One per call | Mark missing; never use zero | `cpu_pct` | Rename; retain original key and value | Map alias |
| `metrics.link_utilization_pct` | `35.3` | Percent, inferred from key | One per call | Mark missing; never use zero | `link_utilization_pct` | Copy value | Map; link ID is not supplied |
| `metrics.packet_loss_pct` | `0.0` | Percent, inferred from key | One per call | Mark missing; never use zero | `packet_loss_pct` | Copy value | Map; observed zero is a real value |
| `metrics.latency_ms` | `14.73` | Milliseconds, inferred from key | One per call | Mark missing; never use zero | `latency_ms` | Copy value | Map |
| `metrics.jitter_ms` | `2.09` | Milliseconds, inferred from key | One per call | Mark missing; never use zero | `jitter_ms` | Copy value | Map; absent from the hardware-failure sample |
| `metrics.queue_drops` | `7110` | Count; time basis unknown | One per call | Mark missing; never use zero | `queue_drops` | Copy count; do not derive a rate | Map, but counter semantics are unconfirmed |
| `metrics.interface_errors` | `195` | Count; time basis unknown | One per call | Mark missing; never use zero | `interface_errors` | Copy count; do not derive a rate | Map; absent from normal and latency-spike samples |
| `status` | `Healthy` | Severity category | One per call | No diagnostic conclusion from absence | None | Preserve as raw metadata only | Reject as a diagnostic metric; it is not interface status or a root-cause label |

## Structure and limitations

- **Timestamp:** ISO 8601 UTC with a `Z` suffix and fractional seconds.
- **Sampling:** one timestamp per generator call; no fixed interval or repeated
  measurements are present. A five-minute window cannot be calculated from
  these saved files.
- **Topology:** only `device_id` is present. No links, `link_id`, capacity,
  or affected-segment relationships are supplied.
- **Counters versus rates:** `queue_drops` and `interface_errors` are counts,
  but the generator output does not say whether they are cumulative counters
  or counts for an interval. A single observation cannot establish a delta
  or rate. Keep the original counts and flag this uncertainty.
- **Units:** metric suffixes indicate percent and milliseconds, but the JSON
  contains no separate unit declarations. Confirm these meanings against the
  notebook code before treating them as verified.
- **Source identity:** no raw `source_id` is supplied. An adapter-assigned
  source name would identify this generator, not an independent sensor.
- **Labels:** `status` is severity. The generation parameter `fault_type`
  is absent from the JSON, so these files do not provide root-cause labels.
- **Preservation:** for every mapped metric, retain the exact raw key path
  as `original_field` and its unchanged raw value as `original_value`.