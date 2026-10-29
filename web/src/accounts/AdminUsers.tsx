// Admin tab "users": the Google accounts. Search, open one, sign it out everywhere, block, make admin, delete.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useDeferredValue, useState } from "react";
import { Loader, TZ } from "../ui";
import { call, Card, post, Stat } from "../pages/Admin";

interface User {
  id: string;
  name: string;
  email: string;
  picture: string | null;
  role: string;
  locale: string;
  blocked: boolean;
  digest_opt_in: boolean;
  gender: string | null;
  created_at: string;
  last_login_at: string;
  sessions: number;
  saved?: number;
  alerts?: number;
}

const day = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString("en-GB", { timeZone: TZ, dateStyle: "medium", timeStyle: "short" }) : "—";

function Stats() {
  const q = useQuery({ queryKey: ["users-stats"], queryFn: () => call("/admin/api/users/stats?days=30") });
  const d = q.data;
  if (!d) return <Loader />;
  const peak = Math.max(1, ...d.per_day.map((x: any) => x.n));
  return (
    <>
      <Card title="Accounts">
        <div className="adm-stats">
          <Stat label="Total" value={d.total} />
          <Stat label="New, 7 days" value={d.new_7} />
          <Stat label="New, 30 days" value={d.new_30} />
          <Stat label="Weekly digest" value={d.digest} hint="opted in" />
          <Stat label="Admins" value={d.admins} />
          <Stat label="Blocked" value={d.blocked} />
        </div>
      </Card>
      <Card title="Sign-ups per day (30 days)" wide>
        <div className="adm-chart" role="img" aria-label={`Sign-ups per day, peak ${peak}`}>
          {d.per_day.map((x: any) => <span key={x.day} style={{ height: `${(x.n / peak) * 100}%` }} title={`${x.day}: ${x.n}`} />)}
        </div>
      </Card>
    </>
  );
}

function Detail({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["user", id], queryFn: () => call(`/admin/api/users/${id}`) });
  const [confirm, setConfirm] = useState("");
  const act = useMutation({
    mutationFn: ({ path, body, method }: { path: string; body?: unknown; method?: string }) =>
      method ? call(`/admin/api/users/${id}${path}`, { method }) : post(`/admin/api/users/${id}${path}`, body),
    onSuccess: (_, v) => {
      qc.invalidateQueries({ queryKey: ["users"] });
      qc.invalidateQueries({ queryKey: ["users-stats"] });
      if (v.method === "DELETE") onClose();
      else qc.invalidateQueries({ queryKey: ["user", id] });
    },
  });
  const u = q.data;
  if (!u) return <Loader />;
  return (
    <Card title={u.name} wide>
      <table className="adm-table">
        <tbody>
          <tr><td>Email</td><td>{u.email}</td></tr>
          <tr><td>Id</td><td><code>{u.id}</code></td></tr>
          <tr><td>Role</td><td>{u.role}{u.blocked && <> · <b className="adm-warn">blocked {day(u.blocked_at)}</b></>}</td></tr>
          <tr><td>Joined / last sign-in</td><td>{day(u.created_at)} / {day(u.last_login_at)}</td></tr>
          <tr><td>Gender</td><td>{u.gender ?? "not asked"}</td></tr>
          <tr><td>Weekly digest</td><td>{u.digest_opt_in ? `yes, since ${day(u.digest_opt_in_at)}` : "no"}</td></tr>
          <tr><td>Signed-in browsers</td><td>{u.sessions}</td></tr>
          {"saved" in u && <tr><td>Saved events</td><td>{u.saved}</td></tr>}
          {"alerts" in u && <tr><td>Alerts</td><td>{u.alerts}</td></tr>}
        </tbody>
      </table>
      <div className="adm-bar">
        <button className="adm-btn ghost" onClick={() => act.mutate({ path: "/revoke" })}>Sign out everywhere</button>
        <button className="adm-btn ghost" onClick={() => act.mutate({ path: u.blocked ? "/unblock" : "/block" })}>{u.blocked ? "Unblock" : "Block"}</button>
        <button className="adm-btn ghost" onClick={() => act.mutate({ path: "/role", body: { role: u.role === "admin" ? "user" : "admin" } })}>
          {u.role === "admin" ? "Make user" : "Make admin"}
        </button>
        <button className="adm-btn ghost" onClick={onClose}>Close</button>
      </div>
      <div className="adm-bar">
        <input value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder="Type the email to delete" aria-label="Type the email to delete" />
        <button className="adm-btn danger" disabled={confirm.trim().toLowerCase() !== u.email} onClick={() => act.mutate({ path: "", method: "DELETE" })}>
          Delete account
        </button>
      </div>
      {act.isError && <p className="adm-err">{String(act.error)}</p>}
    </Card>
  );
}

export function AdminUsers() {
  const [text, setText] = useState("");
  const q = useDeferredValue(text.trim());
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<string | null>(null);
  const list = useQuery({
    queryKey: ["users", q, page],
    queryFn: () => call<{ total: number; page_size: number; items: User[] }>(`/admin/api/users?q=${encodeURIComponent(q)}&page=${page}`),
  });
  const pages = list.data ? Math.max(1, Math.ceil(list.data.total / list.data.page_size)) : 1;
  return (
    <div className="adm-grid">
      <Stats />
      {open && <Detail id={open} onClose={() => setOpen(null)} />}
      <Card title={`Users${list.data ? ` (${list.data.total})` : ""}`} wide>
        <div className="adm-bar">
          <input value={text} onChange={(e) => { setText(e.target.value); setPage(1); }} placeholder="Search email or name" aria-label="Search users" />
        </div>
        {list.isError && <p className="adm-err">{String(list.error)}</p>}
        {!list.data ? <Loader /> : (
          <table className="adm-table">
            <thead><tr><th>Name</th><th>Email</th><th>Joined</th><th>Last sign-in</th><th>Digest</th><th>Browsers</th><th>Role</th></tr></thead>
            <tbody>
              {list.data.items.map((u) => (
                <tr key={u.id} className={u.blocked ? "adm-muted" : ""}>
                  <td><button className="link-btn" onClick={() => setOpen(u.id)}>{u.name}</button></td>
                  <td>{u.email}</td>
                  <td>{day(u.created_at)}</td>
                  <td>{day(u.last_login_at)}</td>
                  <td>{u.digest_opt_in ? "✓" : ""}</td>
                  <td>{u.sessions}</td>
                  <td>{u.blocked ? "blocked" : u.role}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {pages > 1 && (
          <div className="adm-bar">
            <button className="adm-btn ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>‹ Newer</button>
            <span>{page} / {pages}</span>
            <button className="adm-btn ghost" disabled={page >= pages} onClick={() => setPage(page + 1)}>Older ›</button>
          </div>
        )}
      </Card>
    </div>
  );
}
