import { describe, expect, it } from "vitest";
import { MAX_MESSAGE_BYTES, parseServerMessage, ProtocolError } from "../src/protocol";

const timestamp = "2026-08-29T12:00:00+00:00";
const capabilities = {
  browser: false, desktop_control: false, desktop_read: true,
  filesystem_read: true, filesystem_write: false, obsidian_read: true,
  obsidian_write: false, vision: true,
};

describe("server protocol validation", () => {
  it("accepts a closed security snapshot", () => {
    expect(parseServerMessage(JSON.stringify({ type: "security.update", timestamp, sequence: 4, kill_switch_tripped: false, capabilities }))).toMatchObject({ type: "security.update", sequence: 4 });
  });

  it.each([
    { type: "chat.response", timestamp, text: "ok", extra: true },
    { type: "state.update", timestamp, state: "invented", sequence: 1, source: "core" },
    { type: "security.update", timestamp, sequence: 1, kill_switch_tripped: false, capabilities: { VISION: "yes" } },
    { type: "pong", timestamp: "2026-08-29T12:00:00" },
    { type: "pong" },
  ])("rejects invalid or open fields", (payload) => {
    expect(() => parseServerMessage(JSON.stringify(payload))).toThrow(ProtocolError);
  });

  it("enforces the UTF-8 64 KiB boundary", () => {
    expect(() => parseServerMessage("x".repeat(MAX_MESSAGE_BYTES + 1))).toThrow(/64 KiB/);
  });
});
