// The admin's "submissions" tab: events users posted on /submit (/admin/api/community). Nothing a user posts is
// listed before it is approved here (community.auto_publish off): approve, reject with a reason (the poster sees it
// and may edit and send again), take down (rejected for good), or delete.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../home_pins/api";

interface TicketData { name: string; url: string; price: number | null; is_free: boolean; sold_out: boolean }
interface Row {
  id: number;
  status: "pending" | "published" | "rejected" | "removed";
  title: string;
  start_date: string;
  reject_reason: string | null;
  wants_sponsored: boolean;
  event_path: string | null;
  created_at: string;
  updated_at: string | null;
  user: { id: string; name: string; email: string };
  data: {
    description?: string; organizer?: string; artists?: string[]; min_age?: number | null;
    start_time?: string | null; end_date?: string | null; end_time?: string | null; doors_open?: string | null;
    venue_name?: string; address?: string; city?: string; event_types?: string[]; genres?: string[];
    tickets?: TicketData[]; free_entry?: boolean; pictures?: string[]; sponsor_phone?: string; terms_accepted_at?: string;
  };
}

const API = "/admin/api/community";
const STATES = ["pending", "published", "rejected", "removed", "all"] as const;
const hm = (t?: string | null) => (t ? t.slice(0, 5) : ""); // "21:00:00" -> "21:00"
const LABEL: Record<string, string> = { pending: "⏳ pending", published: "✅ live", rejected: "↩️ rejected", removed: "⛔ taken down" };

export function AdminCommunity() {
  const qc = useQueryClient();
  const [state, setState] = useState<(typeof STATES)[number]>("pending");
  const key = ["admin-community", state];
  const list = useQuery({ queryKey: key, queryFn: () => api<Row[]>(state === "all" ? API : `${API}?status=${state}`) });
  const done = () => qc.invalidateQueries({ queryKey: ["admin-community"] });
  const [err, setErr] = useState("");
  const onError = (e: unknown) => setErr(String(e));
  const approve = useMutation({ mutationFn: (id: number) => api(`${API}/${id}/approve`, "POST"), onSuccess: done, onError });
  const reject = useMutation({
    mutationFn: ({ id, reason, take_down }: { id: number; reason: string; take_down: boolean }) => api(`${API}/${id}/reject`, "POST", { reason, take_down }),
    onSuccess: done, onError,
  });
  const remove = useMutation({ mutationFn: (id: number) => api(`${API}/${id}`, "DELETE"), onSuccess: done, onError });
  const ask = (id: number, take_down: boolean) => {
    const reason = prompt(take_down ? "Why is it taken down? (the poster sees this; they can't send it again)" : "Why is it rejected? (the poster sees this and can fix it)");
    if (reason && reason.trim().length >= 2) reject.mutate({ id, reason: reason.trim(), take_down });
  };

  return (
    <section className="adm-card">
      <h2>Events users posted <small>(nothing is listed before you approve it)</small></h2>
      <div className="adm-sp-actions" style={{ marginBottom: 12, flexWrap: "wrap" }}>
        {STATES.map((s) => (
          <button key={s} className={`adm-btn${state === s ? "" : " ghost"}`} onClick={() => setState(s)}>{LABEL[s] ?? s}</button>
        ))}
      </div>
      {err && <p className="adm-err">{err}</p>}
      {list.isError && <p className="adm-err">{String(list.error)}</p>}
      {!list.data && !list.isError && <p>Loading…</p>}
      {list.data && !list.data.length && <p>Nothing here. The form is at <a href="/submit">/submit</a>.</p>}
      <ul className="adm-sp-list">
        {list.data?.map((r) => {
          const d = r.data ?? {};
          const when = [r.start_date, hm(d.start_time), d.end_time ? `– ${d.end_date && d.end_date !== r.start_date ? `${d.end_date} ` : ""}${hm(d.end_time)}` : ""].join(" ");
          return (
            <li key={r.id} style={{ gridTemplateColumns: "1fr auto" }}>
              <div style={{ display: "grid", gap: 4, minWidth: 0 }}>
                <b dir="auto">{LABEL[r.status]} · {r.title}</b>
                <small>🗓 {when}{d.doors_open && ` (doors ${hm(d.doors_open)})`} · 📍 <span dir="auto">{d.venue_name}{d.address && `, ${d.address}`}, {d.city}</span></small>
                <small>👤 {r.user.name} · <a href={`mailto:${r.user.email}`}>{r.user.email}</a> · sent {new Date(r.created_at).toLocaleString("en-GB")}
                  {d.terms_accepted_at && ` · terms ✓ ${new Date(d.terms_accepted_at).toLocaleString("en-GB")}`}</small>
                {(d.event_types?.length || d.genres?.length) ? <small>🏷 {[...(d.event_types ?? []), ...(d.genres ?? [])].join(", ")}</small> : null}
                {d.organizer && <small dir="auto">🎬 {d.organizer}</small>}
                {d.artists?.length ? <small dir="auto">🎤 {d.artists.join(", ")}</small> : null}
                {d.min_age != null && <small>🔞 {d.min_age}+</small>}
                {d.free_entry && <small>🆓 free entry, no ticket</small>}
                {d.tickets?.map((t, i) => (
                  <small key={i}>🎟 <span dir="auto">{t.name}</span> · {t.is_free ? "free" : t.price != null ? `₪${t.price}` : "no price"}{t.sold_out && " · sold out"} · <a href={t.url} target="_blank" rel="noreferrer noopener nofollow" dir="ltr">{t.url}</a></small>
                ))}
                {r.wants_sponsored && <small>💰 wants a sponsored link · <a href={`tel:${d.sponsor_phone}`} dir="ltr">{d.sponsor_phone}</a></small>}
                {d.description && <small style={{ whiteSpace: "pre-wrap", maxHeight: 160, overflow: "auto" }} dir="auto">{d.description}</small>}
                {d.pictures?.length ? (
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {d.pictures.map((p) => <a key={p} href={`/community/uploads/${p}.webp`} target="_blank" rel="noreferrer"><img src={`/community/uploads/${p}/640.webp`} alt="" style={{ height: 70, borderRadius: 8 }} /></a>)}
                  </div>
                ) : null}
                {r.reject_reason && <small>✋ {r.reject_reason}</small>}
                {r.event_path && <small><a href={r.event_path} target="_blank" rel="noreferrer">Open the listing →</a></small>}
              </div>
              <div className="adm-sp-actions" style={{ flexDirection: "column", alignItems: "stretch" }}>
                {r.status !== "published" && r.status !== "removed" && (
                  <button className="adm-btn" disabled={approve.isPending} onClick={() => approve.mutate(r.id)}>Approve</button>
                )}
                {r.status !== "removed" && <button className="adm-btn ghost" onClick={() => ask(r.id, false)}>Reject…</button>}
                {r.status !== "removed" && <button className="adm-btn ghost" onClick={() => ask(r.id, true)}>Take down…</button>}
                <button className="adm-btn danger" onClick={() => confirm(`Delete "${r.title}" and its pictures?`) && remove.mutate(r.id)}>Delete</button>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
