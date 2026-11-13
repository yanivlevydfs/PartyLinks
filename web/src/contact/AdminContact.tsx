// The admin's "requests" tab, second card: messages from the /contact form, newest first (/admin/api/contact-messages).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../home_pins/api";
import { TZ } from "../ui";

interface Msg {
  id: number;
  name: string;
  email: string;
  subject: string | null;
  page: string | null;
  message: string;
  lang: string;
  status: "new" | "answered" | "closed";
  created_at: string;
}

const API = "/admin/api/contact-messages";
const when = (iso: string) =>
  new Date(iso).toLocaleString("en-GB", { timeZone: TZ, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

export function AdminContact() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["contact-messages"], queryFn: () => api<Msg[]>(API) });
  const done = () => qc.invalidateQueries({ queryKey: ["contact-messages"] });
  const setStatus = useMutation({ mutationFn: ({ id, status }: { id: number; status: string }) => api(`${API}/${id}`, "PATCH", { status }), onSuccess: done });
  const remove = useMutation({ mutationFn: (id: number) => api(`${API}/${id}`, "DELETE"), onSuccess: done });

  if (list.isError) return <p className="adm-err">{String(list.error)}</p>;
  if (!list.data) return <p>Loading…</p>;
  return (
    <section className="adm-card">
      <h2>Contact messages <small>({list.data.filter((m) => m.status === "new").length} new)</small></h2>
      {!list.data.length && <p>No messages yet. The form is at <a href="/contact">/contact</a>.</p>}
      <ul className="adm-sp-list">
        {list.data.map((m) => (
          <li key={m.id} className={m.status === "closed" ? "off" : ""} style={{ gridTemplateColumns: "1fr auto" }}>
            <div>
              <b dir="auto">{m.status === "new" && "🆕 "}{m.name}{m.subject && ` · ${m.subject}`}</b>
              <small>
                <a href={`mailto:${m.email}?subject=${encodeURIComponent(`Re: ${m.subject ?? "SPOTX"}`)}`}>{m.email}</a> · {when(m.created_at)} · {m.lang}
              </small>
              {m.page && <small>🔗 <a href={m.page} target="_blank" rel="noreferrer noopener" dir="ltr">{m.page}</a></small>}
              <small style={{ whiteSpace: "pre-wrap" }} dir="auto">💬 {m.message}</small>
            </div>
            <div className="adm-sp-actions">
              <select value={m.status} onChange={(e) => setStatus.mutate({ id: m.id, status: e.target.value })} aria-label="Status">
                <option value="new">new</option>
                <option value="answered">answered</option>
                <option value="closed">closed</option>
              </select>
              <button className="adm-btn ghost" onClick={() => confirm(`Delete ${m.name}'s message?`) && remove.mutate(m.id)}>Delete</button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
