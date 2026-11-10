// Admin tabs "features" and "quality", and the overview / maintenance cards built after the console was
// first written: worker liveness, sources the run manager paused, background jobs, bans, orphan artwork.
import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Loader } from "../ui";
import { Action, ago, bytes, call, Card, post, Stat, when } from "../pages/Admin";

const n = (v: number | null | undefined) => (v ?? 0).toLocaleString("en-US");
const pct = (part: number, whole: number) => (whole ? `${Math.round((100 * part) / whole)}%` : "—");

// --------------------------------------------------------------------------- overview cards
/** The worker process that runs the schedules: down = nothing scrapes, no alerts, no emails. */
export function WorkerCard({ data }: { data: any }) {
  const w = data?.scheduler?.worker;
  const paused: string[] = data?.paused_sources ?? [];
  return (
    <Card title="Worker & paused sources">
      <div className="adm-stats">
        {w ? (
          <>
            <Stat label="Worker" value={w.alive ? "alive" : "DOWN"} hint={w.pid ? `pid ${w.pid}` : undefined} />
            <Stat label="Heartbeat" value={w.heartbeat_age_seconds == null ? "—" : `${Math.round(w.heartbeat_age_seconds)}s`} hint="ago" />
            <Stat label="Up since" value={ago(w.started_at) || "—"} hint={when(w.started_at)} />
          </>
        ) : (
          <Stat label="Worker" value="in-process" hint="schedules run in the web process" />
        )}
        <Stat label="Paused sources" value={paused.length} />
      </div>
      {w && !w.alive && <p className="adm-err">The worker is not answering: no scans, alerts or emails run until it restarts.</p>}
      {paused.length > 0 && (
        <>
          <p className="adm-note">Paused by the run manager (repeated bad runs) or by an admin. They stay paused across restarts until resumed.</p>
          <div className="adm-row-actions">
            {paused.map((s) => (
              <Action key={s} label={`▶ ${s}`} kind="ghost" run={() => post(`/admin/api/jobs/${s}/resume`)} />
            ))}
          </div>
        </>
      )}
    </Card>
  );
}

/** Every scheduled job that is not a source: alerts, emails, artwork, place info, artist links, the bot, IndexNow. */
export function JobsCard({ data }: { data: any }) {
  const sources = new Set(Object.keys(data?.events?.per_source ?? {}));
  const jobs: any[] = (data?.scheduler?.jobs ?? []).filter((j: any) => !sources.has(j.id));
  if (!jobs.length) return null;
  return (
    <Card title={`Background jobs (${jobs.length})`} wide>
      <div className="adm-scroll">
        <table className="adm-table">
          <thead><tr><th>Job</th><th>Next run</th><th></th></tr></thead>
          <tbody>
            {jobs.sort((a, b) => String(a.id).localeCompare(String(b.id))).map((j) => (
              <tr key={j.id} className={j.paused ? "off" : ""}>
                <td><code>{j.id}</code>{j.name && j.name !== j.id ? <small> · {j.name}</small> : null}</td>
                <td>{j.paused ? "paused" : `${when(j.next_run_at)} (${ago(j.next_run_at).replace(" ago", "") || "now"})`}</td>
                <td>
                  {j.paused
                    ? <Action label="Resume" kind="ghost" run={() => post(`/admin/api/jobs/${j.id}/resume`)} />
                    : <Action label="Pause" kind="ghost" run={() => post(`/admin/api/jobs/${j.id}/pause`)} />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="adm-note">A paused job stays paused until resumed, a restart or a config apply.</p>
    </Card>
  );
}

// --------------------------------------------------------------------------- maintenance cards
/** Addresses the rate limit / failed sign-ins banned (this process): lift one, or all. */
export function BansCard() {
  const q = useQuery({ queryKey: ["bans"], queryFn: () => call<Record<string, number>>("/admin/api/bans"), refetchInterval: 30_000 });
  const bans = Object.entries(q.data ?? {});
  return (
    <Card title={`Banned addresses (${bans.length})`}>
      {q.isLoading ? <Loader /> : bans.length === 0 ? <p className="adm-note">None.</p> : (
        <table className="adm-table">
          <thead><tr><th>Address</th><th>Minutes left</th><th></th></tr></thead>
          <tbody>
            {bans.map(([ip, min]) => (
              <tr key={ip}>
                <td><code>{ip}</code></td><td>{min}</td>
                <td><Action label="Lift" kind="ghost" run={() => post(`/admin/api/bans/lift?ip=${encodeURIComponent(ip)}`)} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {bans.length > 1 && <Action label="Lift all" kind="ghost" confirm="Lift every ban?" run={() => post("/admin/api/bans/lift")} render={(x) => `Lifted ${x.lifted.length}`} />}
    </Card>
  );
}

/** Artwork files no image row claims: count first, then delete. Retention runs the same sweep. */
export function OrphansCard() {
  const said = (x: any) => (x.skipped ? `Skipped: ${x.skipped}` : `${n(x.files)} files, ${bytes(x.bytes)}`);
  return (
    <Card title="Orphan artwork files">
      <p className="adm-note">Originals or WebP copies on disk that no event image points to.</p>
      <Action label="Count" kind="ghost" run={() => post("/admin/api/maintenance/orphans?dry_run=true")} render={said} />
      <Action label="Delete them" kind="danger" confirm="Delete the orphan files?" run={() => post("/admin/api/maintenance/orphans?dry_run=false")} render={said} />
    </Card>
  );
}

// --------------------------------------------------------------------------- features tab
function Top({ rows }: { rows: { title: string; slug: string; n: number }[] }) {
  if (!rows.length) return <p className="adm-note">None yet.</p>;
  return (
    <table className="adm-table">
      <tbody>
        {rows.map((r) => (
          <tr key={r.slug}><td dir="auto"><a href={`/event/${r.slug}`} target="_blank" rel="noopener noreferrer">{r.title}</a></td><td>{r.n}</td></tr>
        ))}
      </tbody>
    </table>
  );
}

const Kv = ({ data }: { data: Record<string, number> }) => (
  <table className="adm-table">
    <tbody>
      {Object.entries(data).sort((a, b) => b[1] - a[1]).map(([k, v]) => <tr key={k}><td>{k}</td><td>{n(v)}</td></tr>)}
    </tbody>
  </table>
);

export function Features() {
  const [days, setDays] = useState(7);
  const q = useQuery({ queryKey: ["insights-features", days], queryFn: () => call(`/admin/api/insights/features?days=${days}`), refetchInterval: 60_000 });
  const d = q.data;
  const bar: ReactNode = (
    <div className="adm-bar">
      <label>Window <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
        {[1, 7, 30, 90].map((v) => <option key={v} value={v}>{v} day{v > 1 ? "s" : ""}</option>)}
      </select></label>
    </div>
  );
  if (q.isError) return <>{bar}<p className="adm-err">{String(q.error)}</p></>;
  if (!d) return <>{bar}<Loader /></>;
  const lag = d.alerts.cursor_lag_minutes;
  const maxViews = Math.max(1, ...d.views.per_day.map((x: any) => x.views));
  return (
    <>
      {bar}
      <div className="adm-grid">
        <Card title="Users & sign-in">
          <div className="adm-stats">
            <Stat label="Users" value={n(d.users.total)} hint={`+${n(d.users.new)} new`} />
            <Stat label="Active" value={n(d.users.active)} hint={`signed in, ${days}d`} />
            <Stat label="Google" value={n(d.users.google)} />
            <Stat label="Facebook" value={n(d.users.facebook)} />
            <Stat label="Passkeys" value={n(d.users.passkeys)} hint={`${n(d.users.passkey_users)} users`} />
            <Stat label="Blocked" value={n(d.users.blocked)} />
          </div>
        </Card>
        <Card title="Alerts">
          <div className="adm-stats">
            <Stat label="Active" value={n(d.alerts.active)} hint={`of ${n(d.alerts.total)}`} />
            <Stat label="Instant" value={n(d.alerts.instant)} hint="the rest: digest" />
            <Stat label="New" value={n(d.alerts.new)} />
            <Stat label="Matches" value={n(d.alerts.hits)} />
            <Stat label="Instant emails" value={n(d.alerts.instant_sent)} />
            <Stat label="Digest emails" value={n(d.alerts.digest_sent)} />
            <Stat label="Matcher behind" value={lag == null ? "—" : `${lag} min`} hint={when(d.alerts.cursor)} />
          </div>
          {lag != null && lag > 180 && <p className="adm-warn">The alert matcher is over 3 hours behind: check the <code>alerts:match</code> job.</p>}
        </Card>
        <Card title="Saved events (hearts)">
          <div className="adm-stats">
            <Stat label="Saves" value={n(d.saves.total)} hint={`+${n(d.saves.new)}`} />
            <Stat label="Users saving" value={n(d.saves.users)} />
          </div>
          <Top rows={d.saves.top} />
        </Card>
        <Card title="Event views">
          <div className="adm-chart" title="views per day">
            {d.views.per_day.map((x: any) => <span key={x.day} style={{ height: `${(100 * x.views) / maxViews}%` }} title={`${x.day}: ${x.views} views, ${x.visitors} visitors`} />)}
          </div>
          <div className="adm-stats">
            <Stat label="Views" value={n(d.views.per_day.reduce((s: number, x: any) => s + x.views, 0))} />
            <Stat label="Visitors / day" value={n(Math.max(0, ...d.views.per_day.map((x: any) => x.visitors)))} hint="the busiest day" />
          </div>
          <Top rows={d.views.top} />
        </Card>
        <Card title="Plans (invite friends)">
          <div className="adm-stats">
            <Stat label="Plans" value={n(d.plans.total)} hint={`+${n(d.plans.new)}`} />
            <Stat label="Yes" value={n(d.plans.votes.yes)} />
            <Stat label="Maybe" value={n(d.plans.votes.maybe)} />
            <Stat label="No" value={n(d.plans.votes.no)} />
            <Stat label="Rides" value={n(d.plans.rides)} />
          </div>
        </Card>
        <Card title="Drawn artwork">
          <div className="adm-stats">
            <Stat label="Drawn" value={n(d.artwork.drawn)} hint={`+${n(d.artwork.new)}`} />
            <Stat label="Stored" value={bytes(d.artwork.bytes)} hint="WebP in the database" />
            <Stat label="Last drawn" value={ago(d.artwork.last) || "—"} />
            <Stat label="Upcoming without a picture" value={n(d.artwork.upcoming_without_picture)} hint="primary listings" />
          </div>
        </Card>
        <Card title="Place info (hours, phone)">
          <div className="adm-stats">
            <Stat label="Places" value={n(d.place_info.places)} />
            <Stat label="Matched Ontopo" value={n(d.place_info.matched_ontopo)} hint={pct(d.place_info.matched_ontopo, d.place_info.places)} />
            <Stat label="Own site read" value={n(d.place_info.with_site)} />
            <Stat label="Ontopo venues" value={n(d.place_info.ontopo_venues)} hint={`last read ${ago(d.place_info.ontopo_last) || "—"}`} />
          </div>
        </Card>
        <Card title="Deal changes">
          <div className="adm-stats">
            <Stat label="Changes" value={n(Object.values(d.deals.by_kind as Record<string, number>).reduce((s, v) => s + v, 0))} />
          </div>
          <Kv data={d.deals.by_kind} />
          <p className="adm-note">Busiest sources</p>
          <Kv data={d.deals.by_source} />
        </Card>
      </div>
    </>
  );
}

// --------------------------------------------------------------------------- quality tab
const FIELDS = ["map", "genre", "venue", "city", "picture", "price", "area"] as const;

/** Per source, the share of upcoming listings that lack each field. A share that jumps = a drifted scraper. */
export function Quality() {
  const q = useQuery({ queryKey: ["insights-quality"], queryFn: () => call<any[]>("/admin/api/insights/quality") });
  const [sort, setSort] = useState<(typeof FIELDS)[number] | "upcoming">("upcoming");
  if (q.isError) return <p className="adm-err">{String(q.error)}</p>;
  if (!q.data) return <Loader />;
  const share = (r: any, f: string) => (r.upcoming ? r.missing[f] / r.upcoming : 0);
  const rows = [...q.data].sort((a, b) => (sort === "upcoming" ? b.upcoming - a.upcoming : share(b, sort) - share(a, sort)));
  const cell = (r: any, f: string) => {
    const s = share(r, f);
    const cls = s >= 0.5 ? "adm-err" : s >= 0.2 ? "adm-warn" : "";
    return <td key={f} className={cls} title={`${r.missing[f]} of ${r.upcoming}`}>{r.missing[f] ? pct(r.missing[f], r.upcoming) : "·"}</td>;
  };
  return (
    <Card title="Missing data per source (upcoming listings)" wide>
      <p className="adm-note">Share of each source's upcoming listings without the field. Click a header to sort. Price counts free-entry listings as priced.</p>
      <div className="adm-scroll">
        <table className="adm-table">
          <thead>
            <tr>
              <th>Source</th>
              <th className="clickable" onClick={() => setSort("upcoming")}>Upcoming{sort === "upcoming" ? " ↓" : ""}</th>
              {FIELDS.map((f) => <th key={f} className="clickable" onClick={() => setSort(f)}>{f}{sort === f ? " ↓" : ""}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => <tr key={r.source}><td>{r.source}</td><td>{n(r.upcoming)}</td>{FIELDS.map((f) => cell(r, f))}</tr>)}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
