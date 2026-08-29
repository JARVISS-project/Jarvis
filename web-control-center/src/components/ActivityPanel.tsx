import type { ClientSnapshot } from "../remoteClient";

export function ActivityPanel({ snapshot }: { snapshot: ClientSnapshot }) {
  return (
    <section className="page-enter" aria-labelledby="activity-heading">
      <header className="page-heading"><div><p className="eyebrow">Privacy-filtered</p><h1 id="activity-heading">Activity</h1></div><p>Connection and aggregate state events only.</p></header>
      <div className="activity-notice"><strong>Safe timeline</strong><span>No chat text, arguments, paths, memory, credentials, process identifiers, images, or confirmation targets are recorded here.</span></div>
      <div className="timeline">
        {snapshot.activity.length === 0 && <div className="empty-row">No activity in this browser session.</div>}
        {snapshot.activity.map((item) => <article className="timeline-item" key={item.id}><span className={`timeline-dot ${item.kind}`} /><div><strong>{item.summary}</strong><small>{item.kind}</small></div><time>{new Date(item.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time></article>)}
      </div>
    </section>
  );
}
