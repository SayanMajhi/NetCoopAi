/* Generated from schemas/bundle-1.0.json by `npm run gen:types`. Do not edit. */

/**
 * null only for a new incident or an unsolicited finding
 */
export type InReplyTo = string | null;
export type IncidentId = string;
/**
 * Deterministic, unique per incident
 */
export type MessageId = string;
/**
 * Protocol and version
 */
export type Protocol = string;
export type Recipient = string;
export type Sender = string;
/**
 * UTC, ISO 8601
 */
export type Timestamp = string;
export type MessageType = "TASK" | "FINDING" | "QUERY" | "RESPONSE" | "DECISION" | "ERROR";
export type Messages = Envelope[];
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
export type Observations = Observation[];

/**
 * HTTP body for out-of-process agents: the messages being delivered plus the
 * evidence and topology they may cite. Responses use the same shape.
 */
export interface Bundle {
  messages: Messages;
  observations?: Observations;
  topology?: Topology;
}
export interface Envelope {
  extra?: Extra;
  in_reply_to: InReplyTo;
  incident_id: IncidentId;
  message_id: MessageId;
  payload: Payload;
  protocol: Protocol;
  recipient: Recipient;
  sender: Sender;
  timestamp: Timestamp;
  type: MessageType;
}
/**
 * Unknown optional fields preserved from compatible 1.x senders
 */
export interface Extra {
  [k: string]: unknown;
}
export interface Payload {
  [k: string]: unknown;
}
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
export interface Topology {
  [k: string]: unknown;
}
