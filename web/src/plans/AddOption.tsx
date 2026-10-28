// "Which party on Friday?": add another event of the same day to a plan (POST /plans/{id}/events).
// The list is the site's own /events search for that day, so it follows the same filters and limits.
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { EventPage } from "../types";
import { Img, sized, useT, when } from "../ui";
import { usePlanText } from "./i18n";
import type { Plan } from "./types";

export function AddOption({ plan, onAdded }: { plan: Plan; onAdded: (plan: Plan) => void }) {
  const { t } = useT();
  const p = usePlanText();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState("");
  useEffect(() => {
    const id = setTimeout(() => setQ(text.trim()), 300);
    return () => clearTimeout(id);
  }, [text]);
  const e = plan.event;
  const day = e.local_date;
  // The whole day (one request), nearest to the plan's own start first: Friday night's parties, not its morning.
  const qs = new URLSearchParams({ page_size: "50", ...(day ? { date_from: `${day}T00:00:00`, date_to: `${day}T23:59:59` } : {}) });
  if (e.country) qs.set("abroad", "true");
  if (q.length >= 2) qs.set("q", q);
  const { data, isFetching } = useQuery({
    queryKey: ["plan-add", qs.toString()],
    queryFn: async () => (await (await fetch(`/events?${qs}`)).json()) as EventPage,
    enabled: open,
  });
  if (plan.options.length >= 4) return null;
  if (!open) return <button type="button" className="f-pill plan-add" onClick={() => setOpen(true)}>{p.add}</button>;

  const have = new Set(plan.options.map((o) => o.id));
  const start = Date.parse(e.start_at ?? "");
  const gap = (x: { start_at: string | null }) => Math.abs(Date.parse(x.start_at ?? "") - start) || 0;
  const list = (data?.items ?? []).filter((x) => !have.has(x.id)).sort((a, b) => gap(a) - gap(b)).slice(0, 12);
  const add = async (id: string) => {
    setBusy(id);
    const res = await fetch(`/plans/${encodeURIComponent(plan.id)}/events`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ event_id: id }),
    });
    setBusy("");
    if (res.ok) {
      onAdded((await res.json()) as Plan);
      setOpen(false);
      setText("");
    }
  };
  return (
    <section className="plan-addbox" aria-labelledby="plan-add-h">
      <div className="plan-addhead">
        <h2 id="plan-add-h">{p.addTitle}</h2>
        <button type="button" className="f-pill" onClick={() => setOpen(false)}>{p.close}</button>
      </div>
      <input className="search" type="search" value={text} onChange={(ev) => setText(ev.target.value)} placeholder={p.addSearch} aria-label={p.addSearch} />
      {!isFetching && list.length === 0 && <p className="msg">{p.noMore}</p>}
      <ul className="plan-pick-list">
        {list.map((x) => (
          <li key={x.id}>
            <Img src={sized(x.cover_url ?? x.image_url, 640)} alt={x.title} className="plan-thumb" />
            <div>
              <b dir="auto">{x.title}</b>
              <small>{when(x, t, { hour: "2-digit", minute: "2-digit" })}{x.venue_name ? ` · ${x.venue_name}` : ""}</small>
            </div>
            <button type="button" className="f-pill" disabled={!!busy} onClick={() => add(x.id)}>{busy === x.id ? "…" : p.addThis}</button>
          </li>
        ))}
      </ul>
    </section>
  );
}
