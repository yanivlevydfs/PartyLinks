// Admin tab "email": Resend set up?, the on / off per kind, test send, preview as a user, the last runs.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Loader, TZ } from "../ui";
import { call, Card, post, Stat } from "../pages/Admin";

const KINDS = [["welcome", "Welcome (after sign-up)"], ["instant", "Instant alerts (at most hourly)"], ["digest", "Weekly digest"]] as const;
const day = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString("en-GB", { timeZone: TZ, dateStyle: "medium", timeStyle: "short" }) : "—";

function Preview() {
  const [userId, setUserId] = useState("");
  const [kind, setKind] = useState<"digest" | "welcome">("digest");
  const [html, setHtml] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const show = async () => {
    setErr("");
    const token = (() => { try { return sessionStorage.getItem("partylinks-admin-token") ?? ""; } catch { return ""; } })();
    const res = await fetch(`/admin/api/email/preview?user_id=${encodeURIComponent(userId.trim())}&kind=${kind}`,
      { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (res.ok) setHtml(await res.text()); else { setHtml(null); setErr(`${res.status} ${res.statusText}`); }
  };
  return (
    <Card title="Preview as a user" wide>
      <div className="adm-bar">
        <input value={userId} onChange={(e) => setUserId(e.target.value)} placeholder="User id (from the users tab)" aria-label="User id" />
        <select value={kind} onChange={(e) => setKind(e.target.value as "digest" | "welcome")} aria-label="Email">
          <option value="digest">Weekly digest</option>
          <option value="welcome">Welcome</option>
        </select>
        <button className="adm-btn" disabled={!userId.trim()} onClick={show}>Show</button>
      </div>
      {err && <p className="adm-err">{err}</p>}
      {html && <iframe className="adm-mail" title="Email preview" sandbox="" srcDoc={html} />}
    </Card>
  );
}

export function AdminEmail() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["email"], queryFn: () => call("/admin/api/email"), refetchInterval: 30_000 });
  const toggle = useMutation({
    mutationFn: (body: Record<string, boolean>) => post("/admin/api/email/switches", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["email"] }),
  });
  const test = useMutation({
    mutationFn: (kind: string) => post(`/admin/api/email/test?kind=${kind}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["email"] }),
  });
  const d = q.data;
  if (!d) return q.isError ? <p className="adm-err">{String(q.error)}</p> : <Loader />;
  return (
    <div className="adm-grid">
      <Card title="Sending">
        <div className="adm-stats">
          <Stat label="Resend" value={d.ready ? "ready" : "off"} hint={d.key ? "key set" : "PARTYLINKS_MAIL__RESEND_API_KEY missing"} />
          <Stat label="From" value={<small>{d.from}</small>} />
          <Stat label="Digest" value={<small>{d.digest_cron}</small>} hint={d.timezone} />
        </div>
      </Card>
      <Card title="On / off">
        <table className="adm-table">
          <tbody>
            {KINDS.map(([k, name]) => (
              <tr key={k}>
                <td>{name}</td>
                <td>
                  <button className={"adm-btn" + (d.switches[k] ? "" : " ghost")} onClick={() => toggle.mutate({ [k]: !d.switches[k] })}>
                    {d.switches[k] ? "On" : "Off"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="adm-bar">
          <button className="adm-btn ghost" disabled={!d.ready || test.isPending} onClick={() => test.mutate("digest")}>Send me the digest</button>
          <button className="adm-btn ghost" disabled={!d.ready || test.isPending} onClick={() => test.mutate("welcome")}>Send me the welcome</button>
        </div>
        {test.isError && <p className="adm-err">{String(test.error)}</p>}
        {test.data && <p className="adm-note">{test.data.sent ? "Sent to your Google account's address." : "Not sent: see the runs below."}</p>}
      </Card>
      <Preview />
      <Card title="Last runs" wide>
        <table className="adm-table">
          <thead><tr><th>Kind</th><th>Started</th><th>Sent</th><th>Failed</th><th>Skipped</th><th>Error</th></tr></thead>
          <tbody>
            {d.runs.map((r: any, i: number) => (
              <tr key={i}><td>{r.kind}</td><td>{day(r.started_at)}</td><td>{r.sent}</td><td>{r.failed}</td><td>{r.skipped}</td><td className="adm-err small">{r.error}</td></tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
