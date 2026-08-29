import type { ClientSnapshot } from "../remoteClient";

export function SecurityPanel({ snapshot }: { snapshot: ClientSnapshot }) {
  const capabilities = Object.entries(snapshot.security?.capabilities ?? {}).sort(([a], [b]) => a.localeCompare(b));
  return (
    <section className="page-enter" aria-labelledby="security-heading">
      <header className="page-heading"><div><p className="eyebrow">Read only</p><h1 id="security-heading">Security</h1></div><span className="readonly-badge">No controls</span></header>
      <div className={`kill-switch-card ${snapshot.security?.kill_switch_tripped ? "tripped" : ""}`}>
        <div className="shield-mark">◇</div><div><p className="eyebrow">Emergency boundary</p><h2>{snapshot.security ? snapshot.security.kill_switch_tripped ? "Kill switch tripped" : "Kill switch clear" : "Status unavailable"}</h2><p>This view reports Core state. It cannot arm, reset, or bypass the local kill switch.</p></div>
      </div>
      <div className="section-title"><div><h2>Capabilities</h2><p>Reported by Core; changes must be made locally.</p></div><span>{capabilities.length || "--"} reported</span></div>
      <div className="capability-grid">
        {capabilities.length === 0 && <div className="empty-row">No capability snapshot received.</div>}
        {capabilities.map(([name, enabled]) => <article className="capability" key={name}><span className={enabled ? "enabled" : "disabled"}>{enabled ? "Enabled" : "Disabled"}</span><strong>{name.replaceAll("_", " ")}</strong><small>Core controlled</small></article>)}
      </div>
      <div className="boundary-note"><strong>Authority boundary</strong><p>This frontend sends only protocol-defined chat, subscriptions, pairing/authentication, ping, and exact confirmation decisions. It has no local filesystem, shell, desktop, browser automation, or direct system API access.</p></div>
    </section>
  );
}
