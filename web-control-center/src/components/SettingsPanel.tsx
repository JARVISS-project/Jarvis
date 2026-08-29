import { type FormEvent, useState } from "react";
import type { ClientSnapshot, RemoteClient } from "../remoteClient";

export function SettingsPanel({ snapshot, client }: { snapshot: ClientSnapshot; client: RemoteClient }) {
  const [endpoint, setEndpoint] = useState(snapshot.endpoint);
  const [deviceName, setDeviceName] = useState("Web Control Center");
  const [pairingCode, setPairingCode] = useState("");
  const pair = (event: FormEvent) => {
    event.preventDefault();
    const code = pairingCode;
    setPairingCode("");
    client.submitPairing(deviceName, code);
  };
  return (
    <section className="page-enter" aria-labelledby="settings-heading">
      <header className="page-heading"><div><p className="eyebrow">Client configuration</p><h1 id="settings-heading">Settings</h1></div><p>Browser-local preferences and gateway access.</p></header>
      <div className="settings-grid">
        <article className="settings-card">
          <div className="settings-card-head"><div><h2>Remote Gateway</h2><p>Connect directly over WebSocket.</p></div><span className="step">01</span></div>
          <label htmlFor="endpoint">WebSocket address</label>
          <div className="field-action"><input id="endpoint" type="url" value={endpoint} onChange={(event) => setEndpoint(event.target.value)} placeholder="ws://127.0.0.1:8766" spellCheck={false} /><button className="button primary" onClick={() => snapshot.phase === "disconnected" ? client.connect(endpoint) : client.disconnect()}>{snapshot.phase === "disconnected" ? "Connect" : "Disconnect"}</button></div>
          <p className="field-help">Use <code>wss://</code> when connecting beyond loopback. Browser Origin must be allowed by the Gateway.</p>
        </article>
        <article className="settings-card">
          <div className="settings-card-head"><div><h2>Device pairing</h2><p>Submit a code created locally on the JARVIS host.</p></div><span className="step">02</span></div>
          <form onSubmit={pair}>
            <label htmlFor="device-name">Device name</label><input id="device-name" value={deviceName} maxLength={80} onChange={(event) => setDeviceName(event.target.value)} autoComplete="off" />
            <label htmlFor="pairing-code">6-digit pairing code</label><div className="field-action"><input id="pairing-code" className="code-input" value={pairingCode} onChange={(event) => setPairingCode(event.target.value.replace(/\D/g, "").slice(0, 6))} inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code" /><button className="button primary" disabled={snapshot.phase === "disconnected" || pairingCode.length !== 6}>Pair device</button></div>
          </form>
          <p className="field-help">The code is never generated or stored here and is erased from this form immediately after submission.</p>
        </article>
        <article className="settings-card">
          <div className="settings-card-head"><div><h2>Credential vault</h2><p>Encrypted browser storage.</p></div><span className={`vault-state ${snapshot.hasCredential ? "stored" : ""}`}>{snapshot.hasCredential ? "Stored" : "Empty"}</span></div>
          <p className="credential-copy">The device token is encrypted with a non-extractable AES-GCM Web Crypto key persisted in IndexedDB. It never enters React state, web storage, logs, or visible messages.</p>
          <button className="button secondary" disabled={!snapshot.hasCredential} onClick={() => void client.forgetCredential()}>Forget this browser</button>
          <p className="field-help">This removes the browser copy only. Revoke the device locally on the JARVIS host to invalidate its token.</p>
        </article>
        <article className="settings-card">
          <div className="settings-card-head"><div><h2>Reconnect policy</h2><p>Bounded automatic retries after network loss.</p></div><label className="toggle"><input type="checkbox" checked={snapshot.autoReconnect} onChange={(event) => client.setAutoReconnect(event.target.checked)} /><span /><b>{snapshot.autoReconnect ? "On" : "Off"}</b></label></div>
          <p className="credential-copy">Retries back off to 30 seconds. Manual disconnect, protocol failures, and device revocation stop reconnection.</p>
        </article>
      </div>
    </section>
  );
}
