// The admin's "requests" tab: promotion requests from /promote, newest first (/admin/api/promote-requests).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../home_pins/api";
import { TZ } from "../ui";

interface Req {
  id: number;
  name: string;
  business: string | null;
  email: string;
  phone: string;
  events: { id: string; slug: string | null; title: string; venue_name: string | null; start_at: string | null }[];
  placements: string[];
  message: string | null;
  lang: string;
  status: "new" | "contacted" | "closed";
  created_at: string;
}

const API = "/admin/api/promote-requests";
const when = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString("en-GB", { timeZone: TZ, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";

export function AdminPromote() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["promote-requests"], queryFn: () => api<Req[]>(API) });
  const done = () => qc.invalidateQueries({ queryKey: ["promote-requests"] });
  const setStatus = useMutation({ mutationFn: ({ id, status }: { id: number; status: string }) => api(`${API}/${id}`, "PATCH", { status }), onSuccess: done });
  const remove = useMutation({ mutationFn: (id: number) => api(`${API}/${id}`, "DELETE"), onSuccess: done });

  if (list.isError) return <p className="adm-err">{String(list.error)}</p>;
  if (!list.data) return <p>Loading…</p>;
  if (!list.data.length) return <p>No promotion requests yet. The form is at <a href="/promote">/promote</a>.</p>;
  return (
    <section className="adm-card">
      <h2>Promotion requests <small>({list.data.filter((r) => r.status === "new").length} new)</small></h2>
      <ul className="adm-sp-list">
        {list.data.map((r) => (
          <li key={r.id} className={r.status === "closed" ? "off" : ""} style={{ gridTemplateColumns: "1fr auto" }}>
            <div>
              <b>{r.status === "new" && "🆕 "}{r.name}{r.business && ` · ${r.business}`}</b>
              <small>
                <a href={`mailto:${r.email}`}>{r.email}</a> · <a href={`tel:${r.phone}`} dir="ltr">{r.phone}</a> · {when(r.created_at)} · {r.lang}
              </small>
              <small>Wants: {r.placements.join(", ") || "-"}</small>
              {r.events.map((e) => (
                <small key={e.id}>🎟 <a href={`/event/${e.slug ?? encodeURIComponent(e.id)}`} target="_blank" rel="noreferrer" dir="auto">{e.title}</a> · {when(e.start_at)}{e.venue_name && ` · ${e.venue_name}`}</small>
              ))}
              {r.message && <small style={{ whiteSpace: "pre-wrap" }} dir="auto">💬 {r.message}</small>}
            </div>
            <div className="adm-sp-actions">
              <select value={r.status} onChange={(e) => setStatus.mutate({ id: r.id, status: e.target.value })} aria-label="Status">
                <option value="new">new</option>
                <option value="contacted">contacted</option>
                <option value="closed">closed</option>
              </select>
              <button className="adm-btn ghost" onClick={() => confirm(`Delete ${r.name}'s request and their details?`) && remove.mutate(r.id)}>Delete</button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
