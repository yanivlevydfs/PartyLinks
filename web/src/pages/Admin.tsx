// Hidden admin console at /admin (not linked anywhere, noindex). English only: it's for operators.
import { QueryClient, QueryClientProvider, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Loader, TZ } from "../ui";
import { Sponsored } from "./AdminSponsored";

// --------------------------------------------------------------------------- api
const TOKEN_KEY = "partylinks-admin-token";
const readToken = () => {
  try { return sessionStorage.getItem(TOKEN_KEY) ?? ""; } catch { return ""; }
};

class HttpError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function call<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const token = readToken();
  const res = await fetch(path, {
    ...init,
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body?.detail;
    const msg = detail?.errors?.join("\n") ?? (typeof detail === "string" ? detail : res.statusText);
    throw new HttpError(res.status, msg);
  }
  return body as T;
}
export const post = <T = any,>(path: string, body?: unknown) =>
  call<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

// --------------------------------------------------------------------------- format
const when = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString("en-GB", { timeZone: TZ, dateStyle: "medium", timeStyle: "short" }) : "—";
const bytes = (n: number | null | undefined) => {
  if (n == null) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  for (; n >= 1024 && i < units.length - 1; i++) n /= 1024;
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
};
const ago = (iso: string | null | undefined) => {
  if (!iso) return "";
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return min < 1 ? "just now" : min < 60 ? `${min}m ago` : min < 1440 ? `${Math.round(min / 60)}h ago` : `${Math.round(min / 1440)}d ago`;
};

// Own query client: admin data must never be served from the public site's cache.
const adminClient = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: true } } });

// --------------------------------------------------------------------------- page
const TABS = ["overview", "health", "clicks", "sponsored", "sources", "runs", "config", "maintenance"] as const;
type Tab = (typeof TABS)[number];

export default function AdminPage() {
  useEffect(() => {
    const meta = document.createElement("meta");
    meta.name = "robots";
    meta.content = "noindex, nofollow";
    document.head.appendChild(meta);
    const title = document.title;
    document.title = "SPOTX · Admin";
    return () => { meta.remove(); document.title = title; };
  }, []);
  return (
    <QueryClientProvider client={adminClient}>
      <Admin />
    </QueryClientProvider>
  );
}

function Admin() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>(() => (TABS.find((t) => `#${t}` === location.hash) ?? "overview"));
  const ov = useQuery({ queryKey: ["overview"], queryFn: () => call("/admin/api/overview"), refetchInterval: 10_000 });

  useEffect(() => { history.replaceState(null, "", `#${tab}`); }, [tab]);

  const authError = ov.error instanceof HttpError && (ov.error.status === 401 || ov.error.status === 403) ? ov.error : null;
  if (authError) return <Login error={authError} onLogin={() => qc.invalidateQueries()} />;

  const logout = () => {
    try { sessionStorage.removeItem(TOKEN_KEY); } catch {}
    qc.clear();
    qc.invalidateQueries();
  };

  return (
    <main className="admin wrap" dir="ltr" lang="en">
      <div className="adm-head">
        <h1>Admin <span>console</span></h1>
        <div className="adm-clock">
          {ov.data && <>🕘 {when(ov.data.server_time)} <small>({ov.data.timezone})</small></>}
          {readToken() && <button className="adm-btn ghost" onClick={logout}>Log out</button>}
          <Link to="/" className="adm-btn ghost">View site ↗</Link>
        </div>
      </div>
      <nav className="adm-tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{t}</button>
        ))}
      </nav>
      {ov.isError && !authError && <p className="adm-err">{String(ov.error)}</p>}
      {ov.data?.warnings?.map((w: string) => <p key={w} className="adm-warn adm-banner">⚠ {w}</p>)}
      {tab === "overview" && <Overview data={ov.data} />}
      {tab === "health" && <Health />}
      {tab === "clicks" && <Clicks />}
      {tab === "sponsored" && <Sponsored />}
      {tab === "sources" && <Sources />}
      {tab === "runs" && <Runs />}
      {tab === "config" && <Config />}
      {tab === "maintenance" && <Maintenance data={ov.data} />}
    </main>
  );
}

function Login({ error, onLogin }: { error: HttpError; onLogin: () => void }) {
  const [token, setToken] = useState("");
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    try { sessionStorage.setItem(TOKEN_KEY, token.trim()); } catch {}
    onLogin();
  };
  return (
    <main className="admin wrap" dir="ltr" lang="en">
      <form className="adm-login" onSubmit={submit}>
        <h1>Admin <span>console</span></h1>
        {error.status === 403 ? (
          <p className="adm-note">{error.message}. Add <code>PARTYLINKS_ADMIN__TOKEN=…</code> to <code>.env</code> and restart, then sign in with it here.</p>
        ) : (
          <p className="adm-note">{readToken() ? "Wrong token." : "Enter the admin token."}</p>
        )}
        <input type="password" autoComplete="current-password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="Admin token" aria-label="Admin token" autoFocus />
        <button className="adm-btn" disabled={!token.trim()}>Sign in</button>
      </form>
    </main>
  );
}

// --------------------------------------------------------------------------- bits
export function Card({ title, children, wide }: { title: string; children: ReactNode; wide?: boolean }) {
  return (
    <section className={"adm-card" + (wide ? " wide" : "")}>
      <h3>{title}</h3>
      {children}
    </section>
  );
}
export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="adm-stat">
      <small>{label}</small>
      <b>{value}</b>
      {hint && <span>{hint}</span>}
    </div>
  );
}
const Status = ({ s }: { s: string }) => <span className={`adm-status ${s}`}>{s}</span>;

/** Button that runs a mutation, shows its result, and optionally asks for a second click first. */
function Action({ label, run, confirm, kind = "", render }: {
  label: string; run: () => Promise<any>; confirm?: string; kind?: string; render?: (r: any) => ReactNode;
}) {
  const qc = useQueryClient();
  const [armed, setArmed] = useState(false);
  const m = useMutation({ mutationFn: run, onSettled: () => { setArmed(false); qc.invalidateQueries(); } });
  const click = () => (confirm && !armed ? setArmed(true) : m.mutate());
  return (
    <div className="adm-action">
      <button className={`adm-btn ${kind}`} onClick={click} disabled={m.isPending}>
        {m.isPending ? "Working…" : armed ? confirm : label}
      </button>
      {armed && <button className="adm-btn ghost" onClick={() => setArmed(false)}>Cancel</button>}
      {m.isError && <span className="adm-err">{String((m.error as Error).message)}</span>}
      {m.isSuccess && <span className="adm-ok">{render ? render(m.data) : "Done"}</span>}
    </div>
  );
}

// --------------------------------------------------------------------------- tabs
function Overview({ data }: { data: any }) {
  if (!data) return <Loader />;
  const st = data.storage;
  return (
    <div className="adm-grid">
      <Card title="Events">
        <div className="adm-stats">
          <Stat label="Upcoming" value={data.events.upcoming} />
          <Stat label="In database" value={data.events.total} />
          <Stat label="Not on map" value={data.events.upcoming_without_location} />
          <Stat label="Ticket tiers" value={data.tickets} />
        </div>
      </Card>
      <Card title="Storage">
        <div className="adm-stats">
          <Stat label="DB file" value={bytes(st.file_bytes)} hint={data.database} />
          <Stat label="WAL" value={bytes(st.wal_bytes)} />
          <Stat label="Reclaimable" value={bytes(st.free_bytes)} />
          <Stat label="Artwork" value={bytes(data.images.stored_bytes)} hint={`${data.images.stored}/${data.images.total} stored`} />
        </div>
      </Card>
      <Card title="Scheduler">
        <div className="adm-stats">
          <Stat label="State" value={!data.scheduler.enabled ? "disabled" : data.scheduler.paused ? "paused" : "running"} />
          <Stat label="Jobs" value={data.scheduler.jobs.length} />
          <Stat label="Next retention" value={when(data.retention.next_run_at)} />
        </div>
      </Card>
      <Card title="Events per source" wide>
        <table className="adm-table">
          <thead><tr><th>Source</th><th>Upcoming</th><th>Total</th></tr></thead>
          <tbody>
            {Object.entries(data.events.per_source as Record<string, number>).sort((a, b) => b[1] - a[1]).map(([s, n]) => (
              <tr key={s}><td>{s}</td><td>{data.events.upcoming_per_source[s] ?? 0}</td><td>{n}</td></tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

/** Ticket-link clicks counted by the server (GET /go/...). */
function Clicks() {
  const [days, setDays] = useState(30);
  const q = useQuery({ queryKey: ["clicks", days], queryFn: () => call(`/admin/api/clicks?days=${days}`), refetchInterval: 30_000 });
  const d = q.data;
  const peak = Math.max(1, ...(d?.per_day ?? []).map((x: any) => x.clicks));
  const counts = (title: string, data: Record<string, number>) => (
    <Card title={title}>
      <table className="adm-table">
        <tbody>
          {Object.entries(data).map(([k, n]) => <tr key={k}><td>{k}</td><td>{n}</td></tr>)}
        </tbody>
      </table>
    </Card>
  );
  return (
    <>
      <div className="adm-bar">
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Period">
          {[1, 7, 30, 90, 365].map((n) => <option key={n} value={n}>Last {n} day{n > 1 ? "s" : ""}</option>)}
        </select>
      </div>
      {q.isError && <p className="adm-err">{String(q.error)}</p>}
      {!d ? <Loader /> : (
        <div className="adm-grid">
          <Card title="Ticket clicks">
            <div className="adm-stats">
              <Stat label="Today" value={d.today} />
              <Stat label={`Last ${d.days} days`} value={d.total} />
              <Stat label="Per day" value={(d.total / d.days).toFixed(1)} hint="average" />
            </div>
          </Card>
          <Card title="Per day" wide>
            <div className="adm-chart" role="img" aria-label={`Clicks per day, peak ${peak}`}>
              {d.per_day.map((x: any) => (
                <span key={x.date} style={{ height: `${(x.clicks / peak) * 100}%` }} title={`${x.date}: ${x.clicks}`} />
              ))}
            </div>
          </Card>
          {counts("Per seller", d.per_source)}
          {counts("Where clicked", d.per_placement)}
          {counts("Per city", d.per_city)}
          <Card title="Top events" wide>
            <table className="adm-table">
              <thead><tr><th>Event</th><th>Seller</th><th>Clicks</th></tr></thead>
              <tbody>
                {d.top_events.map((e: any) => (
                  <tr key={e.event_id}><td><Link to={`/event/${encodeURIComponent(e.event_id)}`}>{e.title}</Link></td><td>{e.source}</td><td>{e.clicks}</td></tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      )}
    </>
  );
}

const STATE_CLASS: Record<string, string> = { ok: "success", flaky: "partial", down: "failed", running: "running", "no runs": "" };

/** Per source over the last 24 h (what `run_sources.py --report` prints): runs by status, events of
 * the last good run, average time, latest error. Problems first. */
function Health() {
  const runs = useQuery({ queryKey: ["runs", "health"], queryFn: () => call<any[]>("/runs?limit=500"), refetchInterval: 15_000 });
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => call<any[]>("/sources"), refetchInterval: 15_000 });
  if (runs.isError || sources.isError) return <p className="adm-err">{String(runs.error ?? sources.error)}</p>;
  const since = Date.now() - 24 * 3600_000;
  // ponytail: last 500 runs (~40 h at today's ~290 runs/day); a server-side summary if that stops covering 24 h
  const recent = (runs.data ?? []).filter((r) => new Date(r.started_at).getTime() >= since);
  const rows = (sources.data ?? []).filter((s) => s.enabled).map((s) => {
    const items = recent.filter((r) => r.source === s.name); // newest first
    const n = (st: string) => items.filter((r) => r.status === st).length;
    const good = items.find((r) => r.status === "success" || r.status === "partial");
    const done = items.filter((r) => r.finished_at);
    const secs = done.reduce((t, r) => t + (new Date(r.finished_at).getTime() - new Date(r.started_at).getTime()) / 1000, 0) / (done.length || 1);
    const bad = n("failed") + n("blocked");
    const state = s.running ? "running" : !items.length ? "no runs" : good ? (bad ? "flaky" : "ok") : "down";
    return { s, n, good, secs, bad, state, error: items.find((r) => r.error)?.error as string | undefined };
  });
  const order: Record<string, number> = { down: 0, flaky: 1, "no runs": 2, running: 3, ok: 4 };
  rows.sort((a, b) => order[a.state] - order[b.state] || b.bad - a.bad || a.s.name.localeCompare(b.s.name));
  const count = (st: string) => rows.filter((r) => r.state === st).length;
  return (
    <>
      <div className="adm-stats">
        <Stat label="OK" value={count("ok")} hint="last 24 h: every run succeeded" />
        <Stat label="Flaky" value={count("flaky")} hint="some runs failed or were blocked" />
        <Stat label="Down" value={count("down")} hint="no good run in 24 h" />
        <Stat label="Running / no runs" value={`${count("running")} / ${count("no runs")}`} />
      </div>
      <div className="adm-scroll">
        <table className="adm-table">
          <thead><tr><th>Source</th><th>State</th><th>OK</th><th>Partial</th><th>Failed</th><th>Blocked</th><th title="Events found by the latest good run">Events</th><th>Last good run</th><th>Avg time</th><th>Latest error</th></tr></thead>
          <tbody>
            {rows.map(({ s, n, good, secs, state, error }) => (
              <tr key={s.name}>
                <td><b>{s.name}</b></td>
                <td><span className={`adm-status ${STATE_CLASS[state]}`}>{state}</span></td>
                <td>{n("success")}</td><td>{n("partial")}</td><td>{n("failed")}</td><td>{n("blocked")}</td>
                <td>{good ? good.events_found : "—"}</td>
                <td>{good ? ago(good.finished_at ?? good.started_at) : "—"}</td>
                <td>{secs ? (secs < 120 ? `${Math.round(secs)}s` : `${Math.round(secs / 60)}m`) : "—"}</td>
                <td>{error && <div className="adm-err small">{error.split("\n")[0].slice(0, 160)}</div>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Sources() {
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => call<any[]>("/sources"), refetchInterval: 5000 });
  const jobs = useQuery({ queryKey: ["overview"], queryFn: () => call("/admin/api/overview") });
  const paused = new Set<string>((jobs.data?.scheduler.jobs ?? []).filter((j: any) => j.paused).map((j: any) => j.id));
  const schedulerOn = jobs.data?.scheduler.enabled;
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const toggle = (name: string) => setPicked((p) => { const n = new Set(p); n.has(name) ? n.delete(name) : n.add(name); return n; });
  const all = sources.data ?? [];
  const scanned = (r: any) => `Started ${r.started.length}` + (r.busy.length ? `, already running: ${r.busy.join(", ")}` : "");
  if (sources.isError) return <p className="adm-err">{String(sources.error)}</p>;
  return (
    <>
      <div className="adm-bar">
        <Action label="Run full scan" run={() => post("/admin/api/scan")} confirm="Scan every enabled source now?" render={scanned} />
        <Action label={`Run selected (${picked.size})`} kind="ghost" render={scanned}
          run={() => (picked.size ? post("/admin/api/scan", { sources: [...picked] }) : Promise.reject(new Error("Tick the sources to scan first")))} />
        {picked.size > 0 && <button className="adm-btn ghost" onClick={() => setPicked(new Set())}>Clear</button>}
      </div>
      {schedulerOn && (
        <div className="adm-bar">
          <Action label="Pause all schedules" run={() => post("/admin/api/scheduler/pause")} kind="ghost" />
          <Action label="Resume all schedules" run={() => post("/admin/api/scheduler/resume")} kind="ghost" />
          <small className="adm-note">Pausing lasts until restart or config apply. To disable a source for good, set <code>enabled: false</code> in Config.</small>
        </div>
      )}
      <div className="adm-scroll">
        <table className="adm-table">
          <thead>
            <tr>
              <th><input type="checkbox" aria-label="Select all sources" checked={all.length > 0 && picked.size === all.length}
                onChange={(e) => setPicked(e.target.checked ? new Set(all.map((s) => s.name)) : new Set())} /></th>
              <th>Source</th><th>Schedule</th><th>Next run</th><th>Last run</th><th>Found / saved / failed</th><th />
            </tr>
          </thead>
          <tbody>
            {(sources.data ?? []).map((s) => (
              <tr key={s.name} className={s.enabled ? "" : "off"}>
                <td><input type="checkbox" aria-label={`Select ${s.name}`} checked={picked.has(s.name)} onChange={() => toggle(s.name)} /></td>
                <td><b>{s.name}</b>{!s.enabled && <small> disabled</small>}</td>
                <td><code>{s.cron}</code></td>
                <td>{paused.has(s.name) ? <Status s="paused" /> : when(s.next_run_at)}</td>
                <td>
                  {s.running ? <Status s="running" /> : s.last_run ? <Status s={s.last_run.status} /> : "—"}{" "}
                  <small>{ago(s.last_run?.finished_at ?? s.last_run?.started_at)}</small>
                  {s.last_run?.error && <div className="adm-err small">{s.last_run.error}</div>}
                </td>
                <td>{s.last_run ? `${s.last_run.events_found} / ${s.last_run.events_saved} / ${s.last_run.events_failed}` : "—"}</td>
                <td className="adm-row-actions">
                  <Action label="Run now" run={() => post(`/sources/${s.name}/run`)} render={() => "Started"} />
                  {schedulerOn && s.enabled && (paused.has(s.name)
                    ? <Action label="Resume" kind="ghost" run={() => post(`/admin/api/jobs/${s.name}/resume`)} />
                    : <Action label="Pause" kind="ghost" run={() => post(`/admin/api/jobs/${s.name}/pause`)} />)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function DebugCard() {
  const qc = useQueryClient();
  const state = useQuery({ queryKey: ["logging"], queryFn: () => call<{ debug: boolean }>("/admin/api/logging") });
  const on = !!state.data?.debug;
  const flip = useMutation({ mutationFn: () => post("/admin/api/logging", { on: !on }), onSettled: () => qc.invalidateQueries({ queryKey: ["logging"] }) });
  return (
    <Card title="Logging">
      <p className="adm-note">
        Normally only warnings, errors and failed requests are logged. Debug logs everything (every request, scraper HTTP calls,
        details) until the next restart; set <code>logging.debug: true</code> to keep it on.
      </p>
      <div className="adm-action">
        <button className={`adm-btn ${on ? "" : "ghost"}`} onClick={() => flip.mutate()} disabled={flip.isPending || state.isLoading}>
          {on ? "Debug is ON: switch off" : "Switch debug on"}
        </button>
        {flip.isError && <span className="adm-err">{String((flip.error as Error).message)}</span>}
      </div>
    </Card>
  );
}

function Runs() {
  const [source, setSource] = useState("");
  const [status, setStatus] = useState("");
  const qs = new URLSearchParams({ limit: "200", ...(source && { source }), ...(status && { status }) });
  const runs = useQuery({ queryKey: ["runs", qs.toString()], queryFn: () => call<any[]>(`/runs?${qs}`), refetchInterval: 10_000 });
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => call<any[]>("/sources") });
  return (
    <>
      <div className="adm-bar">
        <select value={source} onChange={(e) => setSource(e.target.value)} aria-label="Source">
          <option value="">All sources</option>
          {(sources.data ?? []).map((s) => <option key={s.name}>{s.name}</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
          <option value="">Any status</option>
          {["running", "success", "partial", "failed", "blocked", "interrupted"].map((s) => <option key={s}>{s}</option>)}
        </select>
      </div>
      {runs.isError && <p className="adm-err">{String(runs.error)}</p>}
      <div className="adm-scroll">
        <table className="adm-table">
          <thead><tr><th>#</th><th>Source</th><th>Trigger</th><th>Status</th><th>Started</th><th>Took</th><th>Found / saved / failed</th><th title="Known events whose tickets or prices changed">Changed</th><th title="No longer listed by the source">Removed</th></tr></thead>
          <tbody>
            {(runs.data ?? []).map((r) => (
              <RunRow key={r.id} r={r} />
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function RunRow({ r }: { r: any }) {
  const [open, setOpen] = useState(false);
  const took = r.finished_at ? Math.round((new Date(r.finished_at).getTime() - new Date(r.started_at).getTime()) / 1000) : null;
  const details = r.error || r.failures?.length;
  return (
    <>
      <tr className={details ? "clickable" : ""} onClick={() => details && setOpen(!open)}>
        <td>{r.id}</td>
        <td>{r.source}</td>
        <td>{r.trigger}</td>
        <td><Status s={r.status} /> {details ? <small>{open ? "▾" : "▸"}</small> : null}</td>
        <td>{when(r.started_at)}</td>
        <td>{took == null ? "…" : took < 120 ? `${took}s` : `${Math.round(took / 60)}m`}</td>
        <td>{r.events_found} / {r.events_saved} / {r.events_failed}</td>
        <td>{r.events_changed ?? "—"}</td>
        <td>{r.events_unlisted ?? "—"}</td>
      </tr>
      {open && (
        <tr className="adm-detail">
          <td colSpan={9}>
            {r.error && <pre className="adm-err">{r.error}</pre>}
            {r.failures?.length > 0 && <pre>{r.failures.join("\n")}</pre>}
          </td>
        </tr>
      )}
    </>
  );
}

function Config() {
  const qc = useQueryClient();
  const cfg = useQuery({ queryKey: ["config"], queryFn: () => call("/admin/api/config"), refetchOnWindowFocus: false });
  const [text, setText] = useState<string | null>(null);
  const [check, setCheck] = useState<{ ok: boolean; errors: string[] } | null>(null);
  const [saved, setSaved] = useState<any>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const yamlText = text ?? cfg.data?.yaml ?? "";
  const dirty = text != null && text !== cfg.data?.yaml;

  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setErr(""); setSaved(null);
    try { await fn(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const validate = () => run(async () => setCheck(await post("/admin/api/config/validate", { yaml: yamlText })));
  const save = () => run(async () => {
    const r = await call("/admin/api/config", { method: "PUT", body: JSON.stringify({ yaml: yamlText }) });
    setSaved(r); setCheck(null); setText(null);
    await qc.invalidateQueries();
  });
  const loadBackup = (name: string) => name && run(async () => {
    setText((await call(`/admin/api/config/backups/${encodeURIComponent(name)}`)).yaml);
    setCheck(null);
  });

  if (cfg.isError) return <p className="adm-err">{String(cfg.error)}</p>;
  if (!cfg.data) return <Loader />;
  return (
    <div className="adm-config">
      <div className="adm-bar">
        <code className="adm-path">{cfg.data.path}</code>
        <button className="adm-btn ghost" onClick={validate} disabled={busy}>Validate</button>
        <button className="adm-btn" onClick={save} disabled={busy || !dirty}>Save &amp; apply</button>
        <button className="adm-btn ghost" onClick={() => { setText(null); setCheck(null); }} disabled={busy || !dirty}>Discard changes</button>
        {cfg.data.backups.length > 0 && (
          <select value="" onChange={(e) => loadBackup(e.target.value)} aria-label="Load a backup">
            <option value="">Load backup…</option>
            {cfg.data.backups.map((b: string) => <option key={b}>{b}</option>)}
          </select>
        )}
      </div>
      {check && (check.ok ? <p className="adm-ok">✓ Valid</p> : <pre className="adm-err">{check.errors.join("\n")}</pre>)}
      {err && <pre className="adm-err">{err}</pre>}
      {saved && (
        <p className={saved.applied ? "adm-ok" : "adm-warn"}>
          Saved (previous version kept as {saved.backup}).{" "}
          {saved.applied ? "Applied now." : `Not applied yet: ${saved.reason}`}
          {saved.restart_required?.length > 0 && ` Restart the server for: ${saved.restart_required.join(", ")}.`}
        </p>
      )}
      <textarea className="adm-yaml" spellCheck={false} value={yamlText} onChange={(e) => setText(e.target.value)} aria-label="settings.yaml" />
      <p className="adm-note">
        Environment variables (<code>PARTYLINKS_*</code>, <code>.env</code>) override the file. Saving checks the file, keeps a backup in
        <code> config/backups/</code>, and reloads sources, schedules, enrichment, media and retention without a restart. Changes to
        <code> database</code> or <code>api</code> need a restart.
      </p>
      <details className="adm-effective">
        <summary>Settings in use right now (file + environment, secrets hidden)</summary>
        <pre>{JSON.stringify(cfg.data.effective, null, 2)}</pre>
      </details>
    </div>
  );
}

function Maintenance({ data }: { data: any }) {
  if (!data) return <Loader />;
  const r = data.retention;
  return (
    <div className="adm-grid">
      <Card title="Retention">
        <ul className="adm-list">
          <li>Status: <b>{r.enabled ? "enabled" : "disabled"}</b> · schedule <code>{r.cron}</code> ({data.timezone})</li>
          <li>Deletes events that ended before <b>{r.keep_days_back === 0 ? "today" : `${r.keep_days_back} day(s) ago`}</b> (Israel time), with their tickets and artwork</li>
          <li>Deletes run history older than <b>{r.keep_runs_days} days</b></li>
          <li>Reclaims disk space afterwards: <b>{r.reclaim_space ? "yes" : "no"}</b></li>
          <li>Next run: <b>{when(r.next_run_at)}</b></li>
        </ul>
        <Action label="Purge expired now" run={() => post("/maintenance/retention")} render={(x) => `Removed ${x.removed_events} events`} />
      </Card>
      <Card title="Disk space">
        <div className="adm-stats">
          <Stat label="DB file" value={bytes(data.storage.file_bytes)} />
          <Stat label="WAL" value={bytes(data.storage.wal_bytes)} />
          <Stat label="Reclaimable" value={bytes(data.storage.free_bytes)} />
        </div>
        <Action label="Reclaim free space" run={() => post("/admin/api/maintenance/vacuum")} render={(x) => `Freed ${bytes(x.freed_bytes)}`} />
        <Action
          label="Full vacuum (rebuild file)"
          confirm="Rebuild now? Writes pause until it finishes"
          kind="danger"
          run={() => post("/admin/api/maintenance/vacuum?full=true")}
          render={(x) => `Freed ${bytes(x.freed_bytes)}`}
        />
      </Card>
      <Card title="Map locations">
        <p className="adm-note">
          {data.events.upcoming_without_location} upcoming events have no coordinates. Fills them from manual pins
          (<code>geocoding.venues</code>), other events at the same venue / address, then OpenStreetMap (1 lookup per second, cached).
        </p>
        <Action label="Locate missing events" run={() => post("/admin/api/maintenance/geocode")} render={(x) => `Located ${x.located} of ${x.missing} (${x.lookups} new lookups)`} />
      </Card>
      <Card title="Areas">
        <p className="adm-note">Re-derive every event's area of the city from <code>enrichment.areas</code> (also runs on start and config apply).</p>
        <Action label="Recompute areas" run={() => post("/admin/api/maintenance/areas")} render={(x) => `${x.updated} events updated`} />
      </Card>
      <DebugCard />
      <Card title="Duplicates">
        <p className="adm-note">Recompute which listings from different sources are the same party.</p>
        <Action label="Regroup duplicates" run={() => post("/admin/api/maintenance/regroup")} render={(x) => `${x.merged_groups} merged groups`} />
      </Card>
    </div>
  );
}
