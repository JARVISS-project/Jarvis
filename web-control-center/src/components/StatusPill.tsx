import type { ConnectionPhase } from "../remoteClient";

export function StatusPill({ phase }: { phase: ConnectionPhase }) {
  const label = phase === "connected" ? "Online" : phase === "authenticating" ? "Authenticating" :
    phase === "reconnecting" ? "Reconnecting" : phase === "connecting" ? "Connecting" : "Offline";
  return <span className={`status-pill status-${phase}`}><i />{label}</span>;
}
