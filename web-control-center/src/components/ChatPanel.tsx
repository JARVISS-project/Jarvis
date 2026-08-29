import { type FormEvent, useEffect, useRef, useState } from "react";
import type { ClientSnapshot, RemoteClient } from "../remoteClient";

export function ChatPanel({ snapshot, client }: { snapshot: ClientSnapshot; client: RemoteClient }) {
  const [text, setText] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => endRef.current?.scrollIntoView({ behavior: "smooth" }), [snapshot.chat.length]);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!text.trim() || !snapshot.authenticated) return;
    client.sendChat(text); setText("");
  };
  return (
    <section className="chat-layout page-enter" aria-labelledby="chat-heading">
      <header className="page-heading chat-heading">
        <div><p className="eyebrow">Session channel</p><h1 id="chat-heading">Chat</h1></div>
        <span className="session-badge">Session only</span>
      </header>
      <div className="chat-stream" aria-live="polite">
        {snapshot.chat.length === 0 && <div className="empty-state"><span className="empty-glyph">J</span><h2>Start a focused session</h2><p>Messages stay in this browser session and are not included in Activity.</p></div>}
        {snapshot.chat.map((item) => <article className={`message ${item.role}`} key={item.id}><span>{item.role === "user" ? "You" : "JARVIS"}</span><p>{item.text}</p><time>{new Date(item.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time></article>)}
        <div ref={endRef} />
      </div>
      <form className="composer" onSubmit={submit}>
        <label className="sr-only" htmlFor="chat-message">Message</label>
        <textarea id="chat-message" value={text} onChange={(event) => setText(event.target.value)} maxLength={8192} rows={1} placeholder={snapshot.authenticated ? "Message JARVIS..." : "Connect and authenticate to chat"} disabled={!snapshot.authenticated} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} />
        <button className="send-button" disabled={!snapshot.authenticated || !text.trim()} aria-label="Send message">Send <span>↗</span></button>
      </form>
    </section>
  );
}
