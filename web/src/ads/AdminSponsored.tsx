// Admin tab: which events are paid placements: in the side rails (which side, what order), first in the
// event results they match, or both.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useDeferredValue, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { EventPage, EventSummary } from "../types";
import { eventPath, Img, Loader, sized, TZ } from "../ui";
import { call, Card, post, Stat } from "../pages/Admin";
import "./sponsored.css";

type Side = "right" | "left";
type Placement = "rails" | "results" | "both";
const PLACEMENTS: Record<Placement, string> = { rails: "Side rails", results: "Top of results", both: "Rails + results" };
interface Pick {
  event_id: string;
  side: Side;
  until: string | null;
  placement: Placement;
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
const strip = (picks: Pick[]) => picks.map(({ event_id, side, until, placement }) => ({ event_id, side, until, placement }));
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
      event_id: e.id, side, until: null, placement: "rails", title: e.title, slug: e.slug, start_at: e.start_at,
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
                  <Img src={sized(e.cover_url ?? e.image_url, 640)} alt="" className="adm-sp-thumb" />
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
            {saved.isLoading && <Loader />}
            {!saved.isLoading && !list.length && <p className="adm-note">Nothing here yet. Add events from the search above.</p>}
            <ol className="adm-sp-list">
              {list.map((p, n) => (
                <li key={p.event_id} className={p.live === false ? "off" : ""}>
                  <Img src={sized(p.cover_url, 640)} alt="" className="adm-sp-thumb" />
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
                      Shown in
                      <select value={p.placement} onChange={(e) => update(p.event_id, { placement: e.target.value as Placement })}>
                        {(Object.keys(PLACEMENTS) as Placement[]).map((k) => <option key={k} value={k}>{PLACEMENTS[k]}</option>)}
                      </select>
                    </label>
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
        {save.isSuccess && !dirty && <span className="adm-ok">Saved. Visitors see it on their next page load (within a minute).</span>}
        <span className="adm-note">Side rails: on every page, from 1440px wide as two rails, below that as one strip. Top of results: first in the event list (home, search, city / genre / venue pages) only when it matches the visitor's search and filters, at most 3, marked "Sponsored". Past events and ended campaigns hide by themselves.</span>
      </div>

      <Report />
    </div>
  );
}

interface StatReport {
  since: string;
  until: string;
  views: number;
  clicks: number;
  results_views: number;
  results_clicks: number;
  events: { event_id: string; title: string; views: number; clicks: number; results_views: number; results_clicks: number; first_day: string; last_day: string }[];
}

const ctr = (clicks: number, views: number) => (views ? `${((clicks / views) * 100).toFixed(1)}%` : "—");

/** Views and clicks per sponsored event, to send to advertisers (also as CSV). */
function Report() {
  const [days, setDays] = useState(30);
  const r = useQuery({
    queryKey: ["sponsored-stats", days],
    queryFn: () => call<StatReport>(`/admin/api/sponsored/stats?days=${days}`),
    refetchInterval: 60_000,
  });
  const d = r.data;
  const csv = () => {
    if (!d) return;
    const cell = (v: string | number) => `"${String(v).replace(/"/g, '""')}"`;
    const lines = [
      ["Event", "Event id", "Views", "Clicks", "CTR", "Views in results", "Clicks in results", "First day", "Last day"],
      ...d.events.map((e) => [e.title, e.event_id, e.views, e.clicks, ctr(e.clicks, e.views), e.results_views, e.results_clicks, e.first_day, e.last_day]),
    ].map((row) => row.map(cell).join(","));
    // BOM: Excel opens Hebrew titles correctly.
    const url = URL.createObjectURL(new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: `sponsored-${d.since}-${d.until}.csv` });
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <Card title="Advertiser report" wide>
      <div className="adm-bar">
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Period">
          {[1, 7, 30, 90, 365].map((n) => <option key={n} value={n}>Last {n} day{n > 1 ? "s" : ""}</option>)}
        </select>
        <button className="adm-btn ghost" onClick={csv} disabled={!d?.events.length}>Download CSV</button>
      </div>
      {r.isError && <p className="adm-err">{String(r.error)}</p>}
      {d && (
        <>
          <div className="adm-stats">
            <Stat label="Views" value={d.views.toLocaleString()} hint={`${d.since} → ${d.until}`} />
            <Stat label="Clicks" value={d.clicks.toLocaleString()} />
            <Stat label="CTR" value={ctr(d.clicks, d.views)} hint="clicks / views" />
            <Stat label="Of them in results" value={`${d.results_views.toLocaleString()} / ${d.results_clicks.toLocaleString()}`} hint="views / clicks at the top of the results" />
          </div>
          {d.events.length ? (
            <table className="adm-table">
              <thead><tr><th>Event</th><th>Views</th><th>Clicks</th><th>CTR</th><th>In results</th><th>Days shown</th></tr></thead>
              <tbody>
                {d.events.map((e) => (
                  <tr key={e.event_id}>
                    <td><Link to={eventPath({ id: e.event_id, slug: null })} target="_blank" dir="auto">{e.title}</Link></td>
                    <td>{e.views.toLocaleString()}</td>
                    <td>{e.clicks.toLocaleString()}</td>
                    <td>{ctr(e.clicks, e.views)}</td>
                    <td>{e.results_views.toLocaleString()} / {e.results_clicks.toLocaleString()}</td>
                    <td>{e.first_day === e.last_day ? e.first_day : `${e.first_day} → ${e.last_day}`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <p className="adm-note">No views yet in this period. A view counts when a visitor sees the card on screen (once per visit); every click counts.</p>}
        </>
      )}
    </Card>
  );
}
