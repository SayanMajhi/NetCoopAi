/* Generated from schemas/observation-1.0.json by `npm run gen:types`. Do not edit. */

export type DeviceId = string | null;
/**
 * e.g. EV-014, deterministic per fixture
 */
export type EvidenceId = string;
export type LinkId = string | null;
/**
 * Canonical metric name, see PROTOCOL.md
 */
export type Metric = string;
export type NormalizationSteps = string[];
export type ObservedAt = string;
export type OriginalField = string;
/**
 * This interface was referenced by `Observation`'s JSON-Schema
 * via the `definition` "QualityFlag".
 */
export type QualityFlag =
  | "ok"
  | "missing"
  | "stale"
  | "malformed"
  | "counter_reset"
  | "unit_converted"
  | "derived"
  | "alias_mapped"
  | "out_of_window";
export type QualityFlags = QualityFlag[];
/**
 * Raw sensor/probe identity, e.g. switch-telemetry
 */
export type SourceId = string;
export type Unit = string;
/**
 * null when missing/malformed — never 0
 */
export type Value = number | string | boolean | null;

export interface Observation {
  device_id?: DeviceId;
  evidence_id: EvidenceId;
  link_id?: LinkId;
  metric: Metric;
  normalization_steps?: NormalizationSteps;
  observed_at: ObservedAt;
  original_field: OriginalField;
  original_value?: OriginalValue;
  quality_flags?: QualityFlags;
  source_id: SourceId;
  unit: Unit;
  value: Value;
}
export interface OriginalValue {
  [k: string]: unknown;
}
