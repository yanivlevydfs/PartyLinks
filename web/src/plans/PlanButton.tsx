// "Who's coming with me?" on an event: makes a plan (POST /plans) and opens its page, where the
// creator answers first and sends the link to WhatsApp. A plain link there, not window.open after an
// await, so no popup blocker stands in the way.
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { usePlanText } from "./i18n";
import "./plans.css";

export function PlanButton({ eventId }: { eventId: string }) {
  const p = usePlanText();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const start = async () => {
    setBusy(true);
    setError(false);
    try {
      const res = await fetch("/plans", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ event_id: eventId }) });
      if (!res.ok) throw new Error(String(res.status));
      navigate(`/plan/${(await res.json()).id}`);
    } catch {
      setError(true);
      setBusy(false);
    }
  };
  return (
    <>
      <button type="button" className="plan-btn" onClick={start} disabled={busy}>
        <span aria-hidden>👥</span> {busy ? p.creating : p.button}
      </button>
      {error && <span className="plan-err" role="alert">{p.failed}</span>}
    </>
  );
}
