import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { App } from "../src/App";
import { RemoteClient } from "../src/remoteClient";
import { FakeSocket, MemoryVault } from "./helpers";

const factory = (url: string) => new FakeSocket(url) as unknown as WebSocket;

describe("Control Center UI", () => {
  beforeEach(() => FakeSocket.reset());

  it("renders primary states and erases a submitted pairing code", async () => {
    const user = userEvent.setup(); const client = new RemoteClient(new MemoryVault(), factory);
    render(<App providedClient={client} />);
    expect(screen.getByRole("heading", { name: "Overview" })).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Settings" })[0]);
    await user.click(screen.getByRole("button", { name: "Connect" }));
    FakeSocket.instances[0].open();
    const code = screen.getByLabelText("6-digit pairing code");
    await user.type(code, "123456"); await user.click(screen.getByRole("button", { name: "Pair device" }));
    expect(code).toHaveValue("");
    expect(FakeSocket.instances[0].messages().at(-1)).toMatchObject({ type: "auth.pair", pairing_code: "123456" });
  });

  it("shows read-only security and excludes confirmation target from Activity", async () => {
    const user = userEvent.setup(); const client = new RemoteClient(new MemoryVault(), factory); render(<App providedClient={client} />);
    client.connect(); const socket = FakeSocket.instances[0]; socket.open();
    socket.receive({
      type: "security.update", sequence: 1, kill_switch_tripped: true,
      capabilities: {
        browser: false, desktop_control: false, desktop_read: true,
        filesystem_read: true, filesystem_write: false, obsidian_read: true,
        obsidian_write: false, vision: true,
      },
    });
    await user.click(screen.getAllByRole("button", { name: "Security" })[0]);
    expect(await screen.findByText("Kill switch tripped")).toBeInTheDocument(); expect(screen.getByText("No controls")).toBeInTheDocument();
    socket.receive({ type: "confirmation.request", session_id: "s", challenge_id: "c", action: "delete", target: "sensitive-target", risk: "CRITICAL", prompt_text: "Really delete?", expires_at: 10 });
    expect(await screen.findByRole("alertdialog")).toHaveTextContent("sensitive-target");
    await user.click(screen.getByRole("button", { name: "Deny" }));
    await user.click(screen.getAllByRole("button", { name: "Activity" })[0]);
    expect(screen.queryByText("sensitive-target")).not.toBeInTheDocument();
  });
});
