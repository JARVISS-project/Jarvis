import type { CredentialVault, DeviceCredential } from "../src/credentialStore";

export class MemoryVault implements CredentialVault {
  credential?: DeviceCredential;
  savedTokens: string[] = [];
  clearCalls = 0;
  async hasCredential() { return Boolean(this.credential); }
  async useCredential<T>(consumer: (credential: DeviceCredential) => T | Promise<T>) { return this.credential ? consumer(this.credential) : undefined; }
  async save(deviceId: string, token: string) { this.savedTokens.push(token); this.credential = { deviceId, token }; }
  async clear() { this.clearCalls += 1; this.credential = undefined; }
}

export class FakeSocket {
  static instances: FakeSocket[] = [];
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(public url: string) { FakeSocket.instances.push(this); }
  open() { this.readyState = 1; this.onopen?.(); }
  send(value: string) { this.sent.push(value); }
  close() { const wasClosed = this.readyState === 3; this.readyState = 3; if (!wasClosed) this.onclose?.(); }
  receive(message: Record<string, unknown>) { this.onmessage?.(new MessageEvent("message", { data: JSON.stringify({ timestamp: new Date().toISOString(), ...message }) })); }
  messages() { return this.sent.map((item) => JSON.parse(item) as Record<string, unknown>); }
  static reset() { FakeSocket.instances = []; }
}
