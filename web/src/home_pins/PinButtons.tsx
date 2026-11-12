// Admin-only doors to the pin picker on the site: 📌 on the event page, ➕ in a home row's head.
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useMe } from "../accounts/useMe";
import "../accounts/accounts.css";
import type { Picked } from "./api";
import { usePinText } from "./i18n";
import { PinPicker } from "./PinPicker";

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
        <PinPicker event={event} rail={rail} onDone={onClose} />
        <button type="button" className="link-btn" onClick={onClose}>{p.close}</button>
      </div>
    </div>,
    document.body,
  );
}

function AdminDoor({ label, className, event, rail }: { label: string; className: string; event?: Picked; rail?: string }) {
  const [open, setOpen] = useState(false);
  if (useMe().data?.role !== "admin") return null;
  return (
    <>
      <button type="button" className={className} onClick={() => setOpen(true)}>{label}</button>
      {open && <PinSheet event={event} rail={rail} onClose={() => setOpen(false)} />}
    </>
  );
}

/** The event page's 📌 (admin only). */
export function EventPinButton({ event }: { event: Picked }) {
  return <AdminDoor label={usePinText().p.pinBtn} className="pin-door" event={event} />;
}

/** A home row's ➕ (admin only): search an event, the row already checked. */
export function RailPinButton({ rail }: { rail?: string }) {
  const { p } = usePinText();
  return rail ? <AdminDoor label={p.addBtn} className="pin-door small" rail={rail} /> : null;
}
