// Pick an event (search) and the home rows it leads: the admin tab, the event page's 📌 and a row's ➕.
import { useQuery } from "@tanstack/react-query";
import { useDeferredValue, useState } from "react";
import type { EventPage } from "../types";
import { Img, sized, TZ } from "../ui";
import { api, type Picked, type Pin, useEventPin, usePinChange } from "./api";
import { usePinText } from "./i18n";
import "./pins.css";

export function useDate() {
  const { locale } = usePinText();
  return (iso: string | null) => (iso ? new Date(iso).toLocaleString(locale, { timeZone: TZ, weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "");
}

/** One event's line: picture, title, date and venue. */
export function EventLine({ e }: { e: Pick<Pin, "title" | "start_at" | "venue_name" | "cover_url"> }) {
  const date = useDate();
  return (
    <>
      <Img src={sized(e.cover_url, 640)} alt="" className="pin-thumb" />
      <div className="pin-what"><b dir="auto">{e.title}</b><small>{date(e.start_at)}{e.venue_name && <> · <span dir="auto">{e.venue_name}</span></>}</small></div>
    </>
  );
}

/** ``event`` given: its rows straight away; else a search first. ``rail``: that row already checked. */
export function PinPicker({ event, rail, onDone }: { event?: Picked; rail?: string; onDone?: () => void }) {
  const { p } = usePinText();
  const [picked, setPicked] = useState(event);
  const [q, setQ] = useState("");
  const query = useDeferredValue(q.trim());
  const found = useQuery({
    queryKey: ["pin-search", query],
    queryFn: () => api<EventPage>(`/events?q=${encodeURIComponent(query)}&page_size=8`),
    enabled: !picked && query.length >= 2,
  });
  if (picked) return <Rows key={picked.id} e={picked} rail={rail} onDone={onDone} onChange={event ? undefined : () => setPicked(undefined)} />;
  return (
    <div className="pin-picker">
      <input className="pin-search" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={p.search} aria-label={p.search} autoFocus />
      {found.isError && <p className="adm-err">{p.error}</p>}
      {query.length >= 2 && found.data && (found.data.items.length ? (
        <ul className="pin-list">
          {found.data.items.map((e) => (
            <li key={e.id}><button type="button" className="pin-pick" onClick={() => setPicked(e)}><EventLine e={{ ...e, cover_url: e.cover_url ?? e.image_url }} /></button></li>
          ))}
        </ul>
      ) : <p className="pin-note">{p.none}</p>)}
    </div>
  );
}

function Rows({ e, rail, onDone, onChange }: { e: Picked; rail?: string; onDone?: () => void; onChange?: () => void }) {
  const { p } = usePinText();
  const now = useEventPin(e.id);
  return (
    <div className="pin-picker">
      <div className="pin-head">
        <EventLine e={{ ...e, cover_url: e.cover_url ?? e.image_url ?? null }} />
        {onChange && <button type="button" className="adm-btn ghost" onClick={onChange}>{p.change}</button>}
      </div>
      {now.isError && <p className="adm-err">{p.error}</p>}
      {now.data && <Form e={e} pin={now.data.pin} natural={now.data.natural} rail={rail} onDone={onDone} />}
    </div>
  );
}

function Form({ e, pin, natural, rail, onDone }: { e: Picked; pin: Pin | null; natural: string[]; rail?: string; onDone?: () => void }) {
  const { p, rows } = usePinText();
  const { save, unpin } = usePinChange();
  const [rails, setRails] = useState(() => [...new Set([...(pin?.rails ?? []), ...(rail ? [rail] : [])])]);
  const [until, setUntil] = useState(pin?.until ? new Date(pin.until).toLocaleDateString("en-CA", { timeZone: TZ }) : "");
  const [note, setNote] = useState(pin?.note ?? "");
  const toggle = (k: string) => setRails((r) => (r.includes(k) ? r.filter((x) => x !== k) : [...r, k]));
  const done = { onSuccess: () => onDone?.() };
  const failed = save.isError || unpin.isError;
  return (
    <>
      <fieldset className="pin-rows">
        <legend>{p.rows}</legend>
        {rows.map(([k, title]) => (
          <label key={k}>
            <input type="checkbox" checked={rails.includes(k)} onChange={() => toggle(k)} />
            {title}{natural.includes(k) && <span className="pin-natural" title={p.natural}> ✓</span>}
          </label>
        ))}
        <small className="pin-note">{rails.length ? p.natural : p.auto}</small>
      </fieldset>
      <label className="pin-field">{p.until}<input type="date" value={until} onChange={(x) => setUntil(x.target.value)} /></label>
      <label className="pin-field">{p.note}<input value={note} maxLength={500} onChange={(x) => setNote(x.target.value)} /></label>
      <div className="pin-actions">
        <button type="button" className="adm-btn" disabled={save.isPending}
          onClick={() => save.mutate({ eventId: e.id, rails, until: until ? `${until}T23:59:00` : null, note: note || null }, done)}>{p.save}</button>
        {pin && <button type="button" className="adm-btn ghost danger" disabled={unpin.isPending} onClick={() => unpin.mutate(e.id, done)}>{p.unpin}</button>}
        {save.isSuccess && <span className="adm-ok">{p.saved}</span>}
        {failed && <span className="adm-err">{p.error}: {String((save.error ?? unpin.error)?.message)}</span>}
      </div>
    </>
  );
}
