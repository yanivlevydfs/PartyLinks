// Admin-only doors to the pin picker on the site: 📌 on the event page, ➕ in a home row's head.
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useMe } from "../accounts/useMe";
import "../accounts/accounts.css";
import { type Picked, usePinChange, usePins } from "./api";
import { usePinText } from "./i18n";
import { EventLine, PinPicker } from "./PinPicker";

export function PinSheet({ event, rail, onClose }: { event?: Picked; rail?: string; onClose: () => void }) {
  const { p, lang } = usePinText();
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [onClose]);
  return createPortal(
    <div className="sheet-back" onClick={onClose}>
      <div className="sheet pin-sheet" role="dialog" aria-modal="true" aria-label={p.tab} dir={lang === "he" ? "rtl" : "ltr"} onClick={(e) => e.stopPropagation()}>
        <h2>{p.tab}</h2>
        {rail && !event && <RowPins rail={rail} />}
        <PinPicker event={event} rail={rail} onDone={onClose} />
        <button type="button" className="link-btn" onClick={onClose}>{p.close}</button>
      </div>
    </div>,
    document.body,
  );
}

function AdminDoor({ label, title, className, event, rail }: { label: string; title?: string; className: string; event?: Picked; rail?: string }) {
  const [open, setOpen] = useState(false);
  if (useMe().data?.role !== "admin") return null;
  return (
    <>
      <button type="button" className={className} title={title} aria-label={title ?? label} onClick={() => setOpen(true)}>{label}</button>
      {open && <PinSheet event={event} rail={rail} onClose={() => setOpen(false)} />}
    </>
  );
}

/** The event page's 📌 (admin only). */
export function EventPinButton({ event }: { event: Picked }) {
  return <AdminDoor label={usePinText().p.pinBtn} className="pin-door" event={event} />;
}

/** The events pinned in this row now, each with ✕ (a row's edit sheet). */
function RowPins({ rail }: { rail: string }) {
  const { p } = usePinText();
  const { unpin } = usePinChange();
  const here = (usePins().data ?? []).filter((x) => x.appears.includes(rail));
  return (
    <div className="row-pins">
      <h3>{p.inRow}</h3>
      {here.length ? (
        <ul>
          {here.map((x) => (
            <li key={x.event_id}>
              <EventLine e={{ ...x, title: x.title ?? x.event_id }} />
              <button type="button" className="pin-x" aria-label={p.unpin} title={p.unpin} disabled={unpin.isPending}
                onClick={() => confirm(p.unpinAsk) && unpin.mutate(x.event_id)}>✕</button>
            </li>
          ))}
        </ul>
      ) : <p className="pin-note">{p.noneInRow}</p>}
      {unpin.isError && <p className="pin-err">{p.error}</p>}
      <h3>{p.addToRow}</h3>
    </div>
  );
}

/** A home row's small red ✎ (admin only): its pins (✕ to remove) and a search to add one, the row checked. */
export function RailPinButton({ rail }: { rail?: string }) {
  const { p } = usePinText();
  return rail ? <AdminDoor label="✎" title={p.editRow} className="pin-icon" rail={rail} /> : null;
}
