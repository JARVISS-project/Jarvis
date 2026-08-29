import { beforeEach, describe, expect, it, vi } from "vitest";
import { RemoteClient } from "../src/remoteClient";
import { FakeSocket, MemoryVault } from "./helpers";

const factory = (url: string) => new FakeSocket(url) as unknown as WebSocket;

describe("RemoteClient", () => {
  beforeEach(() => { FakeSocket.reset(); vi.useRealTimers(); });

  it("authenticates from the vault without exposing the token in state", async () => {
    const vault = new MemoryVault(); vault.credential = { deviceId: "dev-1", token: "secret-token" };
    const client = new RemoteClient(vault, factory); await client.initialize(); client.connect();
    const socket = FakeSocket.instances[0]; socket.open(); await Promise.resolve();
    expect(socket.messages()[0]).toMatchObject({ type: "auth.token", device_id: "dev-1", token: "secret-token" });
    expect(JSON.stringify(client.getSnapshot())).not.toContain("secret-token");
    socket.receive({ type: "auth.success", device_id: "dev-1" });
    expect(socket.messages().slice(1).map((item) => item.type)).toEqual(["state.subscribe", "telemetry.subscribe", "security.subscribe"]);
  });

  it("stores pairing token below React state and never creates a code", async () => {
    const vault = new MemoryVault(); const client = new RemoteClient(vault, factory); client.connect();
    const socket = FakeSocket.instances[0]; socket.open(); await Promise.resolve();
    client.submitPairing("Laptop", "123456");
    expect(socket.messages()[0]).toMatchObject({ type: "auth.pair", device_name: "Laptop", pairing_code: "123456" });
    socket.receive({ type: "device.paired", device_id: "dev-2", device_name: "Laptop", token: "one-time-secret" });
    await vi.waitFor(() => expect(vault.savedTokens).toEqual(["one-time-secret"]));
    expect(JSON.stringify(client.getSnapshot())).not.toContain("one-time-secret");
  });

  it("preserves exact Core confirmation IDs", () => {
    const client = new RemoteClient(new MemoryVault(), factory); client.connect(); const socket = FakeSocket.instances[0]; socket.open();
    socket.receive({ type: "confirmation.request", session_id: "core-session", challenge_id: "core-challenge", action: "critical", target: "private target", risk: "CRITICAL", prompt_text: "Approve?", expires_at: 12345 });
    client.respondToConfirmation(true);
    expect(socket.messages().at(-1)).toMatchObject({ type: "confirmation.respond", session_id: "core-session", challenge_id: "core-challenge", confirmed: true });
    expect(JSON.stringify(client.getSnapshot().activity)).not.toContain("private target");
  });

  it("clears credentials and does not reconnect after revocation", async () => {
    vi.useFakeTimers(); const vault = new MemoryVault(); vault.credential = { deviceId: "dev", token: "secret" };
    const client = new RemoteClient(vault, factory); await client.initialize(); client.connect(); const socket = FakeSocket.instances[0]; socket.open(); await Promise.resolve();
    socket.receive({ type: "device.revoked", device_id: "dev" }); await Promise.resolve(); await Promise.resolve();
    vi.advanceTimersByTime(60_000);
    expect(vault.clearCalls).toBe(1); expect(FakeSocket.instances).toHaveLength(1); expect(client.getSnapshot()).toMatchObject({ phase: "disconnected", hasCredential: false, autoReconnect: true });
  });

  it("stops controlled reconnect after the bounded attempt count", async () => {
    vi.useFakeTimers(); const vault = new MemoryVault(); vault.credential = { deviceId: "dev", token: "secret" };
    const client = new RemoteClient(vault, factory); await client.initialize(); client.connect();
    for (let attempt = 0; attempt < 9; attempt += 1) {
      const socket = FakeSocket.instances.at(-1)!; socket.close();
      vi.runOnlyPendingTimers();
    }
    expect(client.getSnapshot().phase).toBe("disconnected");
    expect(client.getSnapshot().error).toMatch(/Reconnect limit reached/);
    expect(FakeSocket.instances).toHaveLength(9);
  });
});
