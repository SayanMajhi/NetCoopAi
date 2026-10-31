# Organizer generator retrieval

- Retrieval date: 2026-10-31 (Asia/Kolkata)
- Original notebook: https://colab.research.google.com/drive/1xFOtO114qWICAxGisK9K6oOHoOxwvGi1
- Notebook copy: Copy of Mock-data-builder-netScoopAI.ipynb [https://colab.research.google.com/drive/1U-Fz-mOqXh5vtevtVP0EZ6E3rRXlbbmB#scrollTo=_5WB7gNZ_A44]
- Generator function changed: No
- Export method: Separate Colab cell called `generate_telemetry()` and saved its returned JSON
- Random seed: Not set
- Labels: Output contains severity `status`; it does not contain the `fault_type` used to generate it

| Scenario | File | Parameter |
| --- | --- | --- |
| Normal | `normal_unseeded.json` | `fault_type="normal"` |
| Latency spike | `latency_spike_unseeded.json` | `fault_type="latency_spike"` |
| Hardware failure | `hardware_failure_unseeded.json` | `fault_type="hardware_failure"` |

## Scenario coverage

I inspected the notebook's generator code. Its three distinct paths are normal, latency_spike, and hardware_failure. I saved one raw JSON output for each path. No other fault-type path was found.

## Data limitations

Each call to `generate_telemetry()` returns one observation for one randomly selected device. The output has one timestamp and no topology links or repeated measurements. Therefore, these raw files cannot directly support five-minute windows or counter deltas.

The project assumes `queue_drops` and `interface_errors` are cumulative counters unless source metadata says they are rates. The organizer generator does not provide metadata confirming which interpretation is correct. Each generated output contains only one reading for a randomly selected device, so these raw files cannot provide a counter delta, per-minute rate, or reset check. Those calculations require at least two time-ordered readings for the same device and counter.