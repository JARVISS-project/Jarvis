export const MAX_MESSAGE_BYTES = 64 * 1024;
const MAX_CHAT_LENGTH = 8 * 1024;
const MAX_ID_LENGTH = 128;
const MAX_SHORT_TEXT_LENGTH = 256;
const REMOTE_CAPABILITIES = new Set([
  "browser", "desktop_control", "desktop_read", "filesystem_read",
  "filesystem_write", "obsidian_read", "obsidian_write", "vision",
]);

export class ProtocolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ProtocolError";
  }
}

type Common = { type: string; timestamp: string; request_id?: string };
export type AgentState =
  | "waiting_wake" | "listening" | "processing" | "speaking"
  | "waiting_followup" | "waiting_repair" | "confirmation" | "executing" | "error";

export type ChatResponse = Common & { type: "chat.response"; text: string };
export type StateUpdate = Common & { type: "state.update"; state: AgentState; sequence: number; source: string };
export type TelemetryUpdate = Common & {
  type: "telemetry.update";
  sequence: number;
  has_completed_turn: boolean;
  turn_ms: number;
  llm_ms: number;
  llm_calls: number;
  tool_ms: number;
  tool_calls: number;
  audit_events: number;
  audit_write_ok: boolean | null;
};
export type SecurityUpdate = Common & {
  type: "security.update";
  sequence: number;
  kill_switch_tripped: boolean;
  capabilities: Record<string, boolean>;
};
export type ConfirmationRequest = Common & {
  type: "confirmation.request";
  session_id: string;
  challenge_id: string;
  action: string;
  target: string;
  risk: string;
  prompt_text: string;
  expires_at: number;
};
export type ErrorMessage = Common & { type: "error" | "auth.failed"; code: string; message: string };
export type AuthSuccess = Common & { type: "auth.success"; device_id: string };
export type DevicePaired = Common & { type: "device.paired"; device_id: string; device_name: string; token: string };
export type DeviceRevoked = Common & { type: "device.revoked"; device_id: string };
export type Pong = Common & { type: "pong" };
export type ServerMessage = ChatResponse | StateUpdate | TelemetryUpdate | SecurityUpdate |
  ConfirmationRequest | ErrorMessage | AuthSuccess | DevicePaired | DeviceRevoked | Pong;

const states = new Set<AgentState>([
  "waiting_wake", "listening", "processing", "speaking", "waiting_followup",
  "waiting_repair", "confirmation", "executing", "error",
]);

const schemas: Record<string, { required: string[]; optional?: string[] }> = {
  "chat.response": { required: ["text"] },
  "state.update": { required: ["state", "sequence", "source"] },
  "telemetry.update": { required: ["sequence", "has_completed_turn", "turn_ms", "llm_ms", "llm_calls", "tool_ms", "tool_calls", "audit_events", "audit_write_ok"] },
  "security.update": { required: ["sequence", "kill_switch_tripped", "capabilities"] },
  "confirmation.request": { required: ["session_id", "challenge_id", "action", "target", "risk", "prompt_text", "expires_at"] },
  error: { required: ["code", "message"] },
  "auth.failed": { required: ["code", "message"] },
  "auth.success": { required: ["device_id"] },
  "device.paired": { required: ["device_id", "device_name", "token"] },
  "device.revoked": { required: ["device_id"] },
  pong: { required: [] },
};

function object(value: unknown): asserts value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new ProtocolError("Message must be a JSON object.");
}
function string(value: unknown, name: string, max: number, allowEmpty = false): asserts value is string {
  if (typeof value !== "string" || value.length > max || (!allowEmpty && !value)) throw new ProtocolError(`Invalid field: ${name}.`);
}
function integer(value: unknown, name: string): asserts value is number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) throw new ProtocolError(`Invalid field: ${name}.`);
}
function bool(value: unknown, name: string): asserts value is boolean {
  if (typeof value !== "boolean") throw new ProtocolError(`Invalid field: ${name}.`);
}

export function parseServerMessage(raw: string): ServerMessage {
  if (!raw || new TextEncoder().encode(raw).byteLength > MAX_MESSAGE_BYTES) throw new ProtocolError("Message exceeds the 64 KiB limit.");
  let data: unknown;
  try { data = JSON.parse(raw); } catch { throw new ProtocolError("Invalid JSON message."); }
  object(data);
  string(data.type, "type", 64);
  const schema = schemas[data.type];
  if (!schema) throw new ProtocolError("Unsupported message type.");
  const allowed = new Set(["type", "timestamp", "request_id", ...schema.required, ...(schema.optional ?? [])]);
  const unknown = Object.keys(data).filter((key) => !allowed.has(key));
  if (unknown.length) throw new ProtocolError(`Unexpected fields: ${unknown.sort().join(", ")}.`);
  for (const field of schema.required) if (!(field in data)) throw new ProtocolError(`Missing field: ${field}.`);
  string(data.timestamp, "timestamp", MAX_SHORT_TEXT_LENGTH);
  if (Number.isNaN(Date.parse(data.timestamp)) || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(data.timestamp)) throw new ProtocolError("Invalid timestamp.");
  if (data.request_id !== undefined) string(data.request_id, "request_id", MAX_ID_LENGTH);

  switch (data.type) {
    case "chat.response": string(data.text, "text", MAX_CHAT_LENGTH, true); break;
    case "state.update":
      string(data.state, "state", 32); if (!states.has(data.state as AgentState)) throw new ProtocolError("Invalid agent state.");
      integer(data.sequence, "sequence"); string(data.source, "source", MAX_SHORT_TEXT_LENGTH); break;
    case "telemetry.update":
      integer(data.sequence, "sequence"); bool(data.has_completed_turn, "has_completed_turn");
      for (const key of ["turn_ms", "llm_ms", "llm_calls", "tool_ms", "tool_calls", "audit_events"]) integer(data[key], key);
      if (data.audit_write_ok !== null) bool(data.audit_write_ok, "audit_write_ok"); break;
    case "security.update": {
      integer(data.sequence, "sequence"); bool(data.kill_switch_tripped, "kill_switch_tripped"); object(data.capabilities);
      const capabilityNames = Object.keys(data.capabilities);
      if (capabilityNames.length !== REMOTE_CAPABILITIES.size || capabilityNames.some((name) => !REMOTE_CAPABILITIES.has(name))) {
        throw new ProtocolError("Invalid capability set.");
      }
      for (const [key, value] of Object.entries(data.capabilities)) { string(key, "capability", 64); bool(value, `capabilities.${key}`); }
      break;
    }
    case "confirmation.request":
      string(data.session_id, "session_id", MAX_ID_LENGTH); string(data.challenge_id, "challenge_id", MAX_ID_LENGTH);
      string(data.action, "action", MAX_SHORT_TEXT_LENGTH); string(data.target, "target", MAX_CHAT_LENGTH);
      string(data.risk, "risk", 32); string(data.prompt_text, "prompt_text", MAX_CHAT_LENGTH);
      if (typeof data.expires_at !== "number" || !Number.isFinite(data.expires_at) || data.expires_at <= 0) throw new ProtocolError("Invalid expires_at."); break;
    case "error": case "auth.failed":
      string(data.code, "code", 64); string(data.message, "message", MAX_CHAT_LENGTH); break;
    case "auth.success": case "device.revoked": string(data.device_id, "device_id", MAX_ID_LENGTH); break;
    case "device.paired":
      string(data.device_id, "device_id", MAX_ID_LENGTH); string(data.device_name, "device_name", 80); string(data.token, "token", MAX_SHORT_TEXT_LENGTH); break;
  }
  return data as ServerMessage;
}

export type OutboundType = "auth.pair" | "auth.token" | "chat.message" | "state.subscribe" |
  "telemetry.subscribe" | "security.subscribe" | "confirmation.respond" | "ping";

export function makeMessage<T extends Record<string, unknown>>(type: OutboundType, body: T, requestId = crypto.randomUUID()): T & Common {
  const message = { type, timestamp: new Date().toISOString(), request_id: requestId, ...body };
  const encoded = JSON.stringify(message);
  if (new TextEncoder().encode(encoded).byteLength > MAX_MESSAGE_BYTES) throw new ProtocolError("Outbound message exceeds the 64 KiB limit.");
  return message;
}
