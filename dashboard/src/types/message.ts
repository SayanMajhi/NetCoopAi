/* Generated from schemas/message-1.0.json by `npm run gen:types`. Do not edit. */

export type Envelope = {
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
};
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

/**
 * Unknown optional fields preserved from compatible 1.x senders
 */
export interface Extra {
  [k: string]: unknown;
}
export interface Payload {
  [k: string]: unknown;
}
