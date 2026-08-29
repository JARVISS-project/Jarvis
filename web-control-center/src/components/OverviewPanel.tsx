import type { ClientSnapshot } from "../remoteClient";

const stateLabels: Record<string, string> = {
  waiting_wake: "Standing by", listening: "Listening", processing: "Thinking", speaking: "Speaking",
  waiting_followup: "Awaiting follow-up", waiting_repair: "Needs clarification", confirmation: "Needs approval",
  executing: "Executing", error: "Attention required",
};
const duration = (value?: number) => value === undefined ? "--" : value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(2)} s`;

export function OverviewPanel({ snapshot }: { snapshot: ClientSnapshot }) {
  const telemetry = snapshot.telemetry;
  const capabilityCount = snapshot.security ? Object.values(snapshot.security.capabilities).filter(Boolean).length : null;
  return (
    <section className="page-enter" aria-labelledby="overview-heading">
      <header className="page-heading"><div><p className="eyebrow">Live readout</p><h1 id="overview-heading">Overview</h1></div><p>Current Core signals, without operational controls.</p></header>
      <div className="overview-hero">
        <div className="agent-orbit" aria-hidden="true"><i /><i /><i /><span>J</span></div>
        <div><p className="eyebrow">Agent state</p><h2>{snapshot.agentState ? stateLabels[snapshot.agentState] : "No signal yet"}</h2><p>{snapshot.authenticated ? "Receiving live status from the Remote Gateway." : "Connect to establish a read-only status stream."}</p></div>
        <span className={`signal-label ${snapshot.agentState ?? "idle"}`}>{snapshot.agentState?.replaceAll("_", " ") ?? "unavailable"}</span>
      </div>
      <div className="metric-grid">
        <article className="metric-card featured"><span>Turn latency</span><strong>{duration(telemetry?.turn_ms)}</strong><small>{telemetry?.has_completed_turn ? "Latest completed turn" : "Awaiting first completed turn"}</small></article>
        <article className="metric-card"><span>LLM</span><strong>{duration(telemetry?.llm_ms)}</strong><small>{telemetry ? `${telemetry.llm_calls} call${telemetry.llm_calls === 1 ? "" : "s"}` : "No data"}</small></article>
        <article className="metric-card"><span>Tools</span><strong>{duration(telemetry?.tool_ms)}</strong><small>{telemetry ? `${telemetry.tool_calls} invocation${telemetry.tool_calls === 1 ? "" : "s"}` : "No data"}</small></article>
        <article className="metric-card"><span>Audit</span><strong className={telemetry?.audit_write_ok === false ? "bad" : ""}>{telemetry?.audit_write_ok == null ? "--" : telemetry.audit_write_ok ? "Healthy" : "Write error"}</strong><small>{telemetry ? `${telemetry.audit_events} recorded events` : "No evidence yet"}</small></article>
      </div>
      <div className="overview-split">
        <article className="plain-card"><p className="eyebrow">Security posture</p><h3>{snapshot.security?.kill_switch_tripped ? "Kill switch tripped" : snapshot.security ? "Kill switch clear" : "Awaiting security signal"}</h3><p>{capabilityCount === null ? "Capability status has not arrived." : `${capabilityCount} capabilities currently reported enabled.`}</p></article>
        <article className="plain-card"><p className="eyebrow">Connection</p><h3>{snapshot.authenticated ? "Authenticated channel" : "Not authenticated"}</h3><p className="truncate">{snapshot.endpoint}</p></article>
      </div>
    </section>
  );
}
