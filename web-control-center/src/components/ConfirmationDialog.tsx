import { useEffect, useState } from "react";
import type { ConfirmationRequest } from "../protocol";

export function ConfirmationDialog({ request, onRespond }: { request: ConfirmationRequest; onRespond: (confirmed: boolean) => void }) {
  const [busy, setBusy] = useState(false);
  useEffect(() => setBusy(false), [request.session_id, request.challenge_id]);
  const respond = (confirmed: boolean) => { setBusy(true); onRespond(confirmed); };
  return (
    <div className="dialog-backdrop" role="presentation">
      <section className="confirmation-dialog" role="alertdialog" aria-modal="true" aria-labelledby="confirmation-title">
        <div className="critical-mark">!</div>
        <p className="eyebrow danger">Critical decision</p>
        <h2 id="confirmation-title">Explicit approval required</h2>
        <p className="confirmation-prompt">{request.prompt_text}</p>
        <dl className="decision-details">
          <div><dt>Action</dt><dd>{request.action}</dd></div>
          <div><dt>Target</dt><dd>{request.target}</dd></div>
          <div><dt>Risk</dt><dd>{request.risk}</dd></div>
        </dl>
        <p className="privacy-note">These details are shown only for this decision and are not added to Activity.</p>
        <div className="dialog-actions">
          <button className="button secondary" disabled={busy} onClick={() => respond(false)}>Deny</button>
          <button className="button danger-button" disabled={busy} onClick={() => respond(true)}>Approve once</button>
        </div>
      </section>
    </div>
  );
}
