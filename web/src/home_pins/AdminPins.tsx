// Admin tab "📌 pins": pin any upcoming event to chosen home rows, and the pins in force (order, edit, unpin).
import { useState } from "react";
import { Link } from "react-router-dom";
import { eventPath, Loader } from "../ui";
import { usePinChange, usePins } from "./api";
import { usePinText } from "./i18n";
import { EventLine, PinPicker } from "./PinPicker";

export function AdminPins() {
  const { p, rows, lang } = usePinText();
  const pins = usePins();
  const { unpin, order } = usePinChange();
  const [editing, setEditing] = useState<string>();
  const [fresh, setFresh] = useState(0); // remounts the picker after a save: an empty search for the next one
  const title = Object.fromEntries(rows);
  const list = pins.data ?? [];
  const move = (n: number, dir: -1 | 1) => {
    const ids = list.map((x) => x.event_id);
    [ids[n], ids[n + dir]] = [ids[n + dir], ids[n]];
    order.mutate(ids);
  };
  return (
    <div className="adm-grid" dir={lang === "he" ? "rtl" : "ltr"} lang={lang}>
      <section className="adm-card wide">
        <h3>{p.tab}</h3>
        <PinPicker key={fresh} onDone={() => setFresh((n) => n + 1)} />
      </section>
      <section className="adm-card wide">
        <h3>{p.pinned} ({list.length})</h3>
        {pins.isLoading && <Loader />}
        {pins.isError && <p className="adm-err">{p.error}</p>}
        {pins.data && !list.length && <p className="pin-note">{p.empty}</p>}
        {(order.isError || unpin.isError) && <p className="adm-err">{p.error}</p>}
        <ol className="pin-list">
          {list.map((x, n) => (
            <li key={x.event_id} className={x.appears.length ? "" : "off"}>
              <div className="pin-head">
                <Link to={eventPath({ id: x.event_id, slug: x.slug })} target="_blank" className="pin-head">
                  <EventLine e={{ ...x, title: x.title ?? x.event_id }} />
                </Link>
                <span className="pin-actions">
                  <button className="adm-btn ghost" title={p.up} disabled={n === 0 || order.isPending} onClick={() => move(n, -1)}>↑</button>
                  <button className="adm-btn ghost" title={p.down} disabled={n === list.length - 1 || order.isPending} onClick={() => move(n, 1)}>↓</button>
                  <button className="adm-btn ghost" onClick={() => setEditing(editing === x.event_id ? undefined : x.event_id)}>{p.edit}</button>
                  <button className="adm-btn ghost danger" title={p.unpin} disabled={unpin.isPending} onClick={() => unpin.mutate(x.event_id)}>✕</button>
                </span>
              </div>
              <small className="pin-note">
                {x.appears.length ? <>{p.shows} {x.appears.map((k) => title[k] ?? k).join(" · ")}</> : p.hidden}
                {x.note && <> · <span dir="auto">{x.note}</span></>}
              </small>
              {editing === x.event_id && (
                <PinPicker event={{ id: x.event_id, slug: x.slug, title: x.title ?? x.event_id, start_at: x.start_at, venue_name: x.venue_name, cover_url: x.cover_url }}
                  onDone={() => setEditing(undefined)} />
              )}
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
