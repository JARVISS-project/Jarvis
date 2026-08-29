import type { CredentialVault } from "./credentialStore";
import {
  makeMessage, parseServerMessage, ProtocolError,
  type AgentState, type ConfirmationRequest, type SecurityUpdate, type TelemetryUpdate,
} from "./protocol";

export type ConnectionPhase = "disconnected" | "connecting" | "authenticating" | "connected" | "reconnecting";
export type ActivityKind = "connection" | "state" | "telemetry" | "security" | "confirmation" | "error";
export type SafeActivity = { id: string; timestamp: string; kind: ActivityKind; summary: string };
export type ChatItem = { id: string; role: "user" | "assistant"; text: string; timestamp: string };
export type ClientSnapshot = {
  phase: ConnectionPhase;
  endpoint: string;
  authenticated: boolean;
  hasCredential: boolean;
  error: string | null;
  agentState: AgentState | null;
  telemetry: TelemetryUpdate | null;
  security: SecurityUpdate | null;
  confirmation: ConfirmationRequest | null;
  chat: ChatItem[];
  activity: SafeActivity[];
  autoReconnect: boolean;
};

type Listener = (snapshot: ClientSnapshot) => void;
type SocketFactory = (url: string) => WebSocket;
const initialSnapshot: ClientSnapshot = {
  phase: "disconnected", endpoint: "ws://127.0.0.1:8766", authenticated: false,
  hasCredential: false, error: null, agentState: null, telemetry: null, security: null,
  confirmation: null, chat: [], activity: [], autoReconnect: true,
};
const MAX_RECONNECT_ATTEMPTS = 8;

export class RemoteClient {
  private socket: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private snapshot: ClientSnapshot = { ...initialSnapshot };
  private manualDisconnect = false;
  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private generation = 0;
  private stateSequence = -1;

  constructor(private readonly vault: CredentialVault, private readonly socketFactory: SocketFactory = (url) => new WebSocket(url)) {}

  async initialize(): Promise<void> {
    this.update({ hasCredential: await this.vault.hasCredential() });
  }
  subscribe(listener: Listener): () => void { this.listeners.add(listener); listener(this.snapshot); return () => this.listeners.delete(listener); }
  getSnapshot(): ClientSnapshot { return this.snapshot; }

  connect(endpoint = this.snapshot.endpoint): void {
    this.reconnectAttempt = 0;
    this.open(endpoint, false);
  }

  private open(endpoint: string, reconnecting: boolean): void {
    if (!/^wss?:\/\//i.test(endpoint)) { this.update({ error: "Use a ws:// or wss:// gateway address." }); return; }
    this.cancelReconnect(); this.manualDisconnect = false; this.generation += 1;
    this.stateSequence = -1;
    const generation = this.generation;
    this.socket?.close();
    this.update({ endpoint, phase: reconnecting ? "reconnecting" : "connecting", error: null, authenticated: false, agentState: null, telemetry: null, security: null, confirmation: null });
    let socket: WebSocket;
    try { socket = this.socketFactory(endpoint); } catch { this.connectionFailed("The gateway address could not be opened."); return; }
    this.socket = socket;
    socket.onopen = () => { if (generation === this.generation) void this.authenticate(); };
    socket.onmessage = (event) => { if (generation === this.generation) void this.receive(event.data); };
    socket.onerror = () => { if (generation === this.generation) this.update({ error: "The WebSocket connection failed." }); };
    socket.onclose = () => { if (generation === this.generation) this.closed(); };
  }

  disconnect(): void {
    this.manualDisconnect = true; this.reconnectAttempt = 0; this.generation += 1; this.cancelReconnect();
    this.socket?.close(); this.socket = null;
    this.update({ phase: "disconnected", authenticated: false, agentState: null, telemetry: null, security: null, confirmation: null, error: null });
    this.activity("connection", "Disconnected by user.");
  }
  dismissError(): void { this.update({ error: null }); }
  setAutoReconnect(enabled: boolean): void { this.update({ autoReconnect: enabled }); if (!enabled) this.cancelReconnect(); }
  async forgetCredential(): Promise<void> {
    this.disconnect();
    await this.vault.clear();
    this.update({ hasCredential: false });
    this.activity("security", "Stored browser credential removed.");
  }

  submitPairing(deviceName: string, pairingCode: string): void {
    if (!/^\d{6}$/.test(pairingCode)) { this.update({ error: "Pairing code must contain exactly 6 digits." }); return; }
    if (!deviceName.trim() || deviceName.length > 80) { this.update({ error: "Enter a device name up to 80 characters." }); return; }
    this.send(makeMessage("auth.pair", { device_name: deviceName.trim(), pairing_code: pairingCode }));
    this.update({ phase: "authenticating", error: null });
  }
  sendChat(text: string): void {
    const value = text.trim();
    if (!value || value.length > 8 * 1024) return;
    const requestId = crypto.randomUUID();
    this.send(makeMessage("chat.message", { text: value }, requestId));
    this.update({ chat: [...this.snapshot.chat, { id: requestId, role: "user", text: value, timestamp: new Date().toISOString() }] });
  }
  respondToConfirmation(confirmed: boolean): void {
    const confirmation = this.snapshot.confirmation;
    if (!confirmation) return;
    this.send(makeMessage("confirmation.respond", {
      session_id: confirmation.session_id,
      challenge_id: confirmation.challenge_id,
      confirmed,
    }));
    this.update({ confirmation: null });
    this.activity("confirmation", confirmed ? "Critical request approved." : "Critical request denied.");
  }
  ping(): void { this.send(makeMessage("ping", {})); }

  private async authenticate(): Promise<void> {
    this.update({ phase: "authenticating", error: null });
    try {
      const used = await this.vault.useCredential(({ deviceId, token }) => {
        this.send(makeMessage("auth.token", { device_id: deviceId, token }));
        return true;
      });
      if (!used) this.update({ phase: "authenticating", hasCredential: false });
    } catch (error) { this.update({ hasCredential: false, error: error instanceof Error ? error.message : "Credential access failed." }); }
  }

  private async receive(raw: unknown): Promise<void> {
    if (typeof raw !== "string") { this.protocolFailure("Binary gateway messages are not accepted."); return; }
    let message;
    try { message = parseServerMessage(raw); } catch (error) {
      this.protocolFailure(error instanceof ProtocolError ? error.message : "Invalid gateway message."); return;
    }
    switch (message.type) {
      case "device.paired":
        try {
          await this.vault.save(message.device_id, message.token);
          this.update({ hasCredential: true });
          this.authenticated(`Paired as ${message.device_name}.`);
        } catch { this.connectionFailed("Pairing succeeded, but the credential could not be stored securely."); }
        break;
      case "auth.success": this.authenticated("Device authenticated."); break;
      case "auth.failed":
        await this.vault.clear(); this.update({ hasCredential: false, error: message.message, authenticated: false, phase: "authenticating" });
        this.activity("error", `Authentication failed (${message.code}).`); break;
      case "device.revoked":
        await this.vault.clear(); this.manualDisconnect = true; this.cancelReconnect();
        this.update({ hasCredential: false, authenticated: false, phase: "disconnected", confirmation: null, error: "This device was revoked. Pair it again locally to reconnect." });
        this.activity("security", "Device access was revoked."); this.socket?.close(); break;
      case "chat.response":
        this.update({ chat: [...this.snapshot.chat, { id: message.request_id ?? crypto.randomUUID(), role: "assistant", text: message.text, timestamp: message.timestamp }] }); break;
      case "state.update":
        if (message.sequence > this.stateSequence) {
          this.stateSequence = message.sequence;
          this.update({ agentState: message.state }); this.activity("state", `Agent state changed to ${message.state.replaceAll("_", " ")}.`);
        } break;
      case "telemetry.update":
        if (!this.snapshot.telemetry || message.sequence > this.snapshot.telemetry.sequence) { this.update({ telemetry: message }); this.activity("telemetry", "Turn telemetry updated."); } break;
      case "security.update":
        if (!this.snapshot.security || message.sequence > this.snapshot.security.sequence) { this.update({ security: message }); this.activity("security", "Security posture updated."); } break;
      case "confirmation.request": this.update({ confirmation: message }); this.activity("confirmation", "Critical confirmation requested."); break;
      case "error": this.update({ error: message.message }); this.activity("error", `Gateway error (${message.code}).`); break;
      case "pong": break;
    }
  }

  private authenticated(summary: string): void {
    this.reconnectAttempt = 0;
    this.update({ phase: "connected", authenticated: true, error: null });
    this.activity("connection", summary);
    this.send(makeMessage("state.subscribe", { subscribe: true }));
    this.send(makeMessage("telemetry.subscribe", { subscribe: true }));
    this.send(makeMessage("security.subscribe", { subscribe: true }));
  }
  private send(message: Record<string, unknown>): void {
    if (!this.socket || this.socket.readyState !== 1) { this.update({ error: "Connect to the gateway before sending." }); return; }
    this.socket.send(JSON.stringify(message));
  }
  private protocolFailure(message: string): void { this.manualDisconnect = true; this.update({ error: `Gateway protocol error: ${message}`, authenticated: false }); this.socket?.close(); }
  private connectionFailed(message: string): void { this.update({ error: message, authenticated: false, phase: "disconnected" }); this.socket?.close(); }
  private closed(): void {
    this.socket = null; this.update({ authenticated: false, agentState: null, telemetry: null, security: null, confirmation: null });
    if (this.manualDisconnect || !this.snapshot.autoReconnect || !this.snapshot.hasCredential) { this.update({ phase: "disconnected" }); return; }
    this.reconnectAttempt += 1;
    if (this.reconnectAttempt > MAX_RECONNECT_ATTEMPTS) {
      this.update({ phase: "disconnected", error: "Reconnect limit reached. Connect manually to try again." });
      return;
    }
    const delay = Math.min(1000 * 2 ** (this.reconnectAttempt - 1), 30_000);
    this.update({ phase: "reconnecting", error: `Connection lost. Retrying in ${Math.round(delay / 1000)}s.` });
    this.reconnectTimer = setTimeout(() => this.open(this.snapshot.endpoint, true), delay);
  }
  private cancelReconnect(): void { if (this.reconnectTimer) clearTimeout(this.reconnectTimer); this.reconnectTimer = null; }
  private activity(kind: ActivityKind, summary: string): void {
    const item = { id: crypto.randomUUID(), timestamp: new Date().toISOString(), kind, summary };
    this.update({ activity: [item, ...this.snapshot.activity].slice(0, 100) });
  }
  private update(patch: Partial<ClientSnapshot>): void { this.snapshot = { ...this.snapshot, ...patch }; this.listeners.forEach((listener) => listener(this.snapshot)); }
}
