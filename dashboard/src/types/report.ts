/* Generated from schemas/report-1.0.json by `npm run gen:types`. Do not edit. */

export type Report = {
  affected_devices?: AffectedDevices;
  affected_segments?: AffectedSegments;
  agent_findings?: AgentFindings;
  agent_trace_id: AgentTraceId;
  alternative_causes?: AlternativeCauses;
  confidence: Confidence;
  confidence_method?: ConfidenceMethod;
  evidence_sources?: EvidenceSources;
  incident_id: IncidentId;
  limitations?: Limitations;
  normalization_log?: NormalizationLog;
  recommended_actions?: RecommendedActions;
  signature_version: SignatureVersion;
  status: Status;
  supporting_evidence?: SupportingEvidence;
  suspected_root_cause: SuspectedRootCause;
  validation_errors?: ValidationErrors;
};
export type AffectedDevices = string[];
export type AffectedSegments = string[];
export type AgentFindings = string[];
export type AgentTraceId = string;
export type AlternativeCauses = string[];
export type Confidence = number | null;
export type ConfidenceMethod = string;
export type EvidenceSources = string[];
export type IncidentId = string;
export type Limitations = string[];
export type NormalizationLog = string[];
export type RecommendedActions = string[];
export type SignatureVersion = string;
/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "Status".
 */
export type Status = "diagnosed" | "probable" | "healthy" | "insufficient_evidence" | "invalid_input" | "failed";
export type EvidenceId = string;
/**
 * Cumulative counters only: increase since the previous sample of the same series. Null for non-counters, the first sample, after a gap, or on a counter reset
 */
export type IntervalDelta = number | null;
export type Metric = string;
export type SourceId = string;
export type Unit = string;
export type Value = number | string | boolean | null;
export type SupportingEvidence = EvidenceItem[];
export type SuspectedRootCause = string | null;
export type ValidationErrors = string[];

/**
 * This interface was referenced by `undefined`'s JSON-Schema
 * via the `definition` "EvidenceItem".
 */
export interface EvidenceItem {
  evidence_id: EvidenceId;
  interval_delta?: IntervalDelta;
  metric: Metric;
  source_id: SourceId;
  unit: Unit;
  value: Value;
}
