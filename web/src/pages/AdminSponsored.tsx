// Admin tab: which events are paid placements in the home page's side rails, on which side and in what order.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useDeferredValue, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { EventPage, EventSummary } from "../types";
import { eventPath, Img, TZ } from "../ui";
import { call, Card, post } from "./Admin";
import "../sponsored.css";

type Side = "right" | "left";
interface Pick {
  event_id: string;
  side: Side;
  until: string | null;
  title: string | null;
  slug: string | null;
  start_at: string | null;
  venue_name: string | null;
  cover_url: string | null;
  live?: boolean; // as last saved; unknown for picks added since
}

const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString("en-CA", { timeZone: TZ }) : "");
const dateTime = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString("en-GB", { timeZone: TZ, weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";
const strip = (picks: Pick[]) => picks.map(({ event_id, side, until }) => ({ event_id, side, until }));
const other = (s: Side): Side => (s === "right" ? "left" : "right");

export function Sponsored() {
  const qc = useQueryClient();
  const saved = useQuery({
    queryKey: ["sponsored"],
    queryFn: () => call<{ enabled: boolean; items: Pick[] }>("/admin/api/sponsored"),
  });
  const [picks, setPicks] = useState<Pick[]>([]);
  useEffect(() => { if (saved.data) setPicks(saved.data.items); }, [saved.data]);
  const dirty = !!saved.data && JSON.stringify(strip(picks)) !== JSON.stringify(strip(saved.data.items));
  const save = useMutation({
    mutationFn: () => call("/admin/api/sponsored", { method: "PUT", body: JSON.stringify(strip(picks)) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sponsored"] }),
  });
  const enabled = !!saved.data?.enabled;
  const toggle = useMutation({
    mutationFn: () => post("/admin/api/sponsored/enabled", { on: !enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sponsored"] }),
  });

  const [q, setQ] = useState("");
  const query = useDeferredValue(q.trim());
  const found = useQuery({
    queryKey: ["sponsored-search", query],
    queryFn: () => call<EventPage>(`/events?q=${encodeURIComponent(query)}&page_size=8`),
    enabled: query.length >= 2,
  });

  const add = (e: EventSummary, side: Side) =>
    setPicks((p) => [...p, {
      event_id: e.id, side, until: null, title: e.title, slug: e.slug, start_at: e.start_at,
      venue_name: e.venue_name, cover_url: e.cover_url ?? e.image_url,
    }]);
  const update = (id: string, change: Partial<Pick>) => setPicks((p) => p.map((x) => (x.event_id === id ? { ...x, ...change } : x)));
  const remove = (id: string) => setPicks((p) => p.filter((x) => x.event_id !== id));
  /** Swap with the previous / next pick on the same side. */
  const move = (id: string, dir: -1 | 1) =>
    setPicks((p) => {
      const at = p.findIndex((x) => x.event_id === id);
      const sameSide = p.map((x, i) => (x.side === p[at].side ? i : -1)).filter((i) => i >= 0);
      const to = sameSide[sameSide.indexOf(at) + dir];
      if (to === undefined) return p;
      const next = [...p];
      [next[at], next[to]] = [next[to], next[at]];
      return next;
    });
  const picked = new Set(picks.map((p) => p.event_id));

  return (
    <div className="adm-grid">
      <Card title="Sponsored on the site" wide>
        <div className="adm-sp-switch">
          <button role="switch" aria-checked={enabled} className={"adm-sp-toggle" + (enabled ? " on" : "")}
            disabled={!saved.data || toggle.isPending} onClick={() => toggle.mutate()}>
            <span aria-hidden />
          </button>
          <div>
            <b>{enabled ? "ON: sponsored events are showing" : "OFF: every sponsored event is hidden"}</b>
            <small>Hides or shows all placements at once on every page. The picks below are kept either way.</small>
          </div>
          {toggle.isError && <span className="adm-err">{String((toggle.error as Error).message)}</span>}
        </div>
      </Card>

      <Card title="Add a sponsored event" wide>
        <input className="adm-sp-search" type="search" value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="Search events by title, artist, venue…" />
        {found.isError && <p className="adm-err">{String(found.error)}</p>}
        {query.length >= 2 && found.data && (
          found.data.items.length ? (
            <ul className="adm-sp-list">
              {found.data.items.map((e) => (
                <li key={e.id}>
                  <Img src={e.cover_url ?? e.image_url} alt="" className="adm-sp-thumb" />
                  <div>
                    <b dir="auto">{e.title}</b>
                    <small>{dateTime(e.start_at)}{e.venue_name && <> · <span dir="auto">{e.venue_name}</span></>} · {e.source}</small>
                  </div>
                  {picked.has(e.id) ? <span className="adm-ok">sponsored</span> : (
                    <span className="adm-sp-actions">
                      <button className="adm-btn" onClick={() => add(e, "right")}>+ Right</button>
                      <button className="adm-btn" onClick={() => add(e, "left")}>+ Left</button>
                    </span>
                  )}
                </li>
              ))}
            </ul>
          ) : <p className="adm-note">No upcoming events match.</p>
        )}
      </Card>

      {(["right", "left"] as const).map((side) => {
        const list = picks.filter((p) => p.side === side);
        return (
          <Card key={side} title={`${side === "right" ? "Right" : "Left"} rail (${list.length})`}>
            {saved.isLoading && <p className="adm-note">Loading…</p>}
            {!saved.isLoading && !list.length && <p className="adm-note">Nothing here yet. Add events from the search above.</p>}
            <ol className="adm-sp-list">
              {list.map((p, n) => (
                <li key={p.event_id} className={p.live === false ? "off" : ""}>
                  <Img src={p.cover_url} alt="" className="adm-sp-thumb" />
                  <div>
                    {p.title
                      ? <Link to={eventPath({ id: p.event_id, slug: p.slug })} target="_blank" dir="auto"><b>{p.title}</b></Link>
                      : <b>{p.event_id} (deleted)</b>}
                    <small>
                      {dateTime(p.start_at)}{p.venue_name && <> · <span dir="auto">{p.venue_name}</span></>}
                      {p.live === true && <span className="adm-status success">live</span>}
                      {p.live === false && <span className="adm-status paused">not showing</span>}
                    </small>
                    <label className="adm-sp-until">
                      Campaign ends
                      <input type="date" value={day(p.until)}
                        onChange={(e) => update(p.event_id, { until: e.target.value ? new Date(`${e.target.value}T23:59`).toISOString() : null })} />
                      {p.until && <button className="adm-btn ghost" onClick={() => update(p.event_id, { until: null })}>clear</button>}
                    </label>
                  </div>
                  <span className="adm-sp-actions">
                    <button className="adm-btn ghost" title="Up" disabled={n === 0} onClick={() => move(p.event_id, -1)}>↑</button>
                    <button className="adm-btn ghost" title="Down" disabled={n === list.length - 1} onClick={() => move(p.event_id, 1)}>↓</button>
                    <button className="adm-btn ghost" title={`Move to the ${other(side)} rail`} onClick={() => update(p.event_id, { side: other(side) })}>⇄</button>
                    <button className="adm-btn ghost danger" title="Remove" onClick={() => remove(p.event_id)}>✕</button>
                  </span>
                </li>
              ))}
            </ol>
          </Card>
        );
      })}

      <div className="adm-sp-save">
        <button className="adm-btn" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? "Saving…" : "Save placements"}
        </button>
        {dirty && <button className="adm-btn ghost" onClick={() => saved.data && setPicks(saved.data.items)}>Discard changes</button>}
        {dirty && <span className="adm-warn">Unsaved changes</span>}
        {save.isError && <span className="adm-err">{String((save.error as Error).message)}</span>}
        {save.isSuccess && !dirty && <span className="adm-ok">Saved. The site shows it within 5 minutes per visitor.</span>}
        <span className="adm-note">Shown on the home page from 1440px wide as two rails, below that as one strip. Past events and ended campaigns hide by themselves.</span>
      </div>
    </div>
  );
}
