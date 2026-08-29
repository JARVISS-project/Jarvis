import { useMemo, useState } from "react";
import { IndexedDbCredentialVault } from "./credentialStore";
import { RemoteClient } from "./remoteClient";
import { useRemoteClient } from "./hooks/useRemoteClient";
import { StatusPill } from "./components/StatusPill";
import { ConfirmationDialog } from "./components/ConfirmationDialog";
import { ChatPanel } from "./components/ChatPanel";
import { OverviewPanel } from "./components/OverviewPanel";
import { ActivityPanel } from "./components/ActivityPanel";
import { SecurityPanel } from "./components/SecurityPanel";
import { SettingsPanel } from "./components/SettingsPanel";

type Tab = "chat" | "overview" | "activity" | "security" | "settings";
const tabs: { id: Tab; label: string; glyph: string }[] = [
  { id: "chat", label: "Chat", glyph: "◌" }, { id: "overview", label: "Overview", glyph: "▦" },
  { id: "activity", label: "Activity", glyph: "⌁" }, { id: "security", label: "Security", glyph: "◇" },
  { id: "settings", label: "Settings", glyph: "⊙" },
];

export function App({ providedClient }: { providedClient?: RemoteClient }) {
  const client = useMemo(() => providedClient ?? new RemoteClient(new IndexedDbCredentialVault()), [providedClient]);
  const snapshot = useRemoteClient(client);
  const [tab, setTab] = useState<Tab>("overview");
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span>J</span><div><strong>JARVIS</strong><small>Control Center</small></div></div>
        <nav aria-label="Primary navigation">{tabs.map((item) => <button key={item.id} className={tab === item.id ? "active" : ""} onClick={() => setTab(item.id)}><span>{item.glyph}</span>{item.label}{item.id === "chat" && snapshot.confirmation && <i className="nav-alert" />}</button>)}</nav>
        <div className="sidebar-foot"><StatusPill phase={snapshot.phase} /><small className="endpoint-label">{snapshot.endpoint.replace(/^wss?:\/\//, "")}</small><p>Remote view<br />No local authority</p></div>
      </aside>
      <header className="mobile-header"><div className="brand"><span>J</span><strong>JARVIS</strong></div><StatusPill phase={snapshot.phase} /></header>
      <main>
        {snapshot.error && <div className="error-banner" role="alert"><strong>Connection notice</strong><span>{snapshot.error}</span><button aria-label="Dismiss error" onClick={() => client.dismissError()}>Dismiss</button></div>}
        {tab === "chat" && <ChatPanel snapshot={snapshot} client={client} />}
        {tab === "overview" && <OverviewPanel snapshot={snapshot} />}
        {tab === "activity" && <ActivityPanel snapshot={snapshot} />}
        {tab === "security" && <SecurityPanel snapshot={snapshot} />}
        {tab === "settings" && <SettingsPanel snapshot={snapshot} client={client} />}
      </main>
      <nav className="mobile-nav" aria-label="Mobile navigation">{tabs.map((item) => <button key={item.id} aria-label={item.label} className={tab === item.id ? "active" : ""} onClick={() => setTab(item.id)}><span>{item.glyph}</span><small>{item.label}</small></button>)}</nav>
      {snapshot.confirmation && <ConfirmationDialog request={snapshot.confirmation} onRespond={(confirmed) => client.respondToConfirmation(confirmed)} />}
    </div>
  );
}
