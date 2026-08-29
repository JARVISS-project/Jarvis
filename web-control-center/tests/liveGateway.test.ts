// @vitest-environment node

import { afterEach, describe, expect, it } from "vitest";
import { RemoteClient } from "../src/remoteClient";
import { MemoryVault } from "./helpers";

const gatewayUrl = process.env.JARVIS_LIVE_GATEWAY_URL;
const pairingCode = process.env.JARVIS_LIVE_PAIRING_CODE;

async function waitFor(predicate: () => boolean, timeoutMs = 5000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error("Live Gateway smoke test timed out.");
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
}

describe.skipIf(!gatewayUrl || !pairingCode)("live WebSocket Gateway", () => {
  let client: RemoteClient | undefined;

  afterEach(() => client?.disconnect());

  it("connects, pairs, observes Core state, and chats through RemoteCorePort", async () => {
    client = new RemoteClient(new MemoryVault());
    await client.initialize();
    client.connect(gatewayUrl!);
    await waitFor(() => client!.getSnapshot().phase === "authenticating");

    client.submitPairing("Control Center Live Test", pairingCode!);
    await waitFor(() => client!.getSnapshot().authenticated);
    await waitFor(() => client!.getSnapshot().security !== null);

    client.sendChat("live control center probe");
    await waitFor(() => client!.getSnapshot().chat.some(
      (message) => message.role === "assistant",
    ));

    const snapshot = client.getSnapshot();
    expect(snapshot.chat.at(-1)?.text).toBe("live core response");
    expect(snapshot.agentState).not.toBeNull();
    expect(snapshot.security).toMatchObject({
      kill_switch_tripped: false,
      capabilities: { desktop_control: false, vision: true },
    });
    expect(snapshot.authenticated).toBe(true);
  }, 10_000);
});
