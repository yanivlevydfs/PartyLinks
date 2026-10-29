// "Who's coming with me?" on an event: makes a plan (POST /plans) and opens its page, where the
// creator answers first and sends the link to WhatsApp. A plain link there, not window.open after an
// await, so no popup blocker stands in the way. ``compact``: the round icon on an event card, beside
// its calendar and share buttons. Signed in with a group on this event already: opens that group.
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { usePlanText } from "./i18n";
import "./plans.css";
import { WRITE_HEADERS } from "./types";
import { useGroups } from "./useGroups";

export function PlanButton({ eventId, compact = false }: { eventId: string; compact?: boolean }) {
  const p = usePlanText();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const mine = useGroups().data?.find((g) => g.event.id === eventId);
  const start = async () => {
    if (mine) return navigate(`/plan/${mine.id}`);
    setBusy(true);
    setError(false);
    try {
      const res = await fetch("/plans", { method: "POST", headers: WRITE_HEADERS, body: JSON.stringify({ event_id: eventId }) });
      if (!res.ok) throw new Error(String(res.status));
      navigate(`/plan/${(await res.json()).id}`);
      void client.invalidateQueries({ queryKey: ["groups"] });
    } catch {
      setError(true);
      setBusy(false);
    }
  };
  if (compact) {
    return (
      <button type="button" className={"plan-btn on-card" + (error ? " err" : "")} onClick={start} disabled={busy}
        aria-label={error ? p.failed : mine ? p.openMine : p.button} title={error ? p.failed : mine ? p.openMine : p.button}>
        <span aria-hidden>👥</span>
      </button>
    );
  }
  return (
    <>
      <button type="button" className="plan-btn" onClick={start} disabled={busy}>
        <span aria-hidden>👥</span> {busy ? p.creating : mine ? p.openMine : p.button}
      </button>
      {error && <span className="plan-err" role="alert">{p.failed}</span>}
    </>
  );
}
