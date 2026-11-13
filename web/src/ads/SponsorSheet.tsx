// Sponsor straight from the site (admins only): 💰 on the event page, ✎ in a side rail's head.
// Same endpoints as the admin tab (/admin/api/sponsored/...); a change refreshes the rails at once.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useDeferredValue, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { useMe } from "../accounts/useMe";
import "../accounts/accounts.css";
import { api, type Picked } from "../home_pins/api";
import { EventLine } from "../home_pins/PinPicker";
import "../home_pins/pins.css";
import type { EventPage } from "../types";
import { TZ } from "../ui";
import { useAdText } from "./i18n";

type Side = "right" | "left";
type Placement = "rails" | "results" | "both";
interface Pick {
  event_id: string;
  side: Side;
  until: string | null;
  placement: Placement;
  title: string | null;
  start_at: string | null;
  venue_name: string | null;
  cover_url: string | null;
  live: boolean;
}

const one = (id: string) => `/admin/api/sponsored/event/${encodeURIComponent(id)}`;

function useAdChange() {
  const qc = useQueryClient();
  const onSuccess = () => ["sponsored", "admin-sponsored", "sponsor-one"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  return {
    save: useMutation({
      mutationFn: ({ id, ...body }: { id: string; side: Side; placement: Placement; until: string | null }) => api(one(id), "PUT", body),
      onSuccess,
    }),
    remove: useMutation({ mutationFn: (id: string) => api(one(id), "DELETE"), onSuccess }),
  };
}

/** The sheet: one event's placement (``event``), or one rail's picks and a search to add (``side``). */
function SponsorSheet({ event, side, onClose }: { event?: Picked; side?: Side; onClose: () => void }) {
  const { a, lang } = useAdText();
  const [picked, setPicked] = useState(event);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [onClose]);
  return createPortal(
    <div className="sheet-back" onClick={onClose}>
      <div className="sheet pin-sheet" role="dialog" aria-modal="true" aria-label={a.title} dir={lang === "he" ? "rtl" : "ltr"} onClick={(e) => e.stopPropagation()}>
        <h2>{a.title}</h2>
        {picked
          ? <Form key={picked.id} e={picked} side={side} onBack={event ? undefined : () => setPicked(undefined)} />
          : side && <SidePicks side={side} onPick={setPicked} />}
        <p className="pin-note"><Link to="/admin#sponsored" onClick={onClose}>{a.report}</Link></p>
        <button type="button" className="link-btn" onClick={onClose}>{a.close}</button>
      </div>
    </div>,
    document.body,
  );
}

/** A rail's sponsored events (✕ to stop one), then a search to add another. */
function SidePicks({ side, onPick }: { side: Side; onPick: (e: Picked) => void }) {
  const { a } = useAdText();
  const { remove } = useAdChange();
  const all = useQuery({ queryKey: ["admin-sponsored"], queryFn: () => api<{ enabled: boolean; items: Pick[] }>("/admin/api/sponsored") });
  const here = (all.data?.items ?? []).filter((x) => x.side === side);
  const [q, setQ] = useState("");
  const query = useDeferredValue(q.trim());
  const found = useQuery({
    queryKey: ["sponsor-search", query],
    queryFn: () => api<EventPage>(`/events?q=${encodeURIComponent(query)}&page_size=8`),
    enabled: query.length >= 2,
  });
  const taken = new Set((all.data?.items ?? []).map((x) => x.event_id));
  return (
    <div className="pin-picker">
      {all.data && !all.data.enabled && <p className="pin-err">{a.off}</p>}
      <div className="row-pins">
        <h3>{a.inSide}</h3>
        {here.length ? (
          <ul>
            {here.map((x) => (
              <li key={x.event_id} className={x.live ? "" : "off"}>
                <EventLine e={{ ...x, title: x.title ?? x.event_id }} />
                <button type="button" className="pin-x" aria-label={a.remove} title={a.remove} disabled={remove.isPending}
                  onClick={() => confirm(a.removeAsk) && remove.mutate(x.event_id)}>✕</button>
              </li>
            ))}
          </ul>
        ) : <p className="pin-note">{a.noneInSide}</p>}
        {remove.isError && <p className="pin-err">{a.error}</p>}
        <h3>{a.add}</h3>
      </div>
      <input className="pin-search" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={a.search} aria-label={a.search} />
      {query.length >= 2 && found.data && (found.data.items.length ? (
        <ul className="pin-list">
          {found.data.items.filter((e) => !taken.has(e.id)).map((e) => (
            <li key={e.id}><button type="button" className="pin-pick" onClick={() => onPick(e)}><EventLine e={{ ...e, cover_url: e.cover_url ?? e.image_url }} /></button></li>
          ))}
        </ul>
      ) : <p className="pin-note">{a.none}</p>)}
    </div>
  );
}

/** One event's side, placement and end date; save, or stop sponsoring it. */
function Form({ e, side, onBack }: { e: Picked; side?: Side; onBack?: () => void }) {
  const { a } = useAdText();
  const { save, remove } = useAdChange();
  const now = useQuery({ queryKey: ["sponsor-one", e.id], queryFn: () => api<{ enabled: boolean; pick: Pick | null }>(one(e.id)) });
  const pick = now.data?.pick;
  const [s, setS] = useState<Side>(side ?? "right");
  const [placement, setPlacement] = useState<Placement>("rails");
  const [until, setUntil] = useState("");
  useEffect(() => {
    if (!pick) return;
    setS(pick.side);
    setPlacement(pick.placement);
    setUntil(pick.until ? new Date(pick.until).toLocaleDateString("en-CA", { timeZone: TZ }) : "");
  }, [pick]);
  return (
    <div className="pin-picker">
      <div className="pin-head">
        <EventLine e={{ ...e, cover_url: e.cover_url ?? e.image_url ?? null }} />
        {onBack && <button type="button" className="adm-btn ghost" onClick={onBack}>←</button>}
      </div>
      {now.data && !now.data.enabled && <p className="pin-err">{a.off}</p>}
      {pick && <p className="pin-note">{pick.live ? `✓ ${a.live}` : a.notLive}</p>}
      <fieldset className="pin-rows">
        <legend>{a.side}</legend>
        {(["right", "left"] as const).map((v) => (
          <label key={v}><input type="radio" name="sp-side" checked={s === v} onChange={() => setS(v)} />{a[v]}</label>
        ))}
      </fieldset>
      <fieldset className="pin-rows">
        <legend>{a.placement}</legend>
        {(["rails", "results", "both"] as const).map((v) => (
          <label key={v}><input type="radio" name="sp-where" checked={placement === v} onChange={() => setPlacement(v)} />{a[v]}</label>
        ))}
      </fieldset>
      <label className="pin-field">{a.until}
        <input type="date" value={until} onChange={(ev) => setUntil(ev.target.value)} />
      </label>
      <div className="pin-actions">
        <button type="button" className="adm-btn" disabled={save.isPending || now.isPending}
          onClick={() => save.mutate({ id: e.id, side: s, placement, until: until ? `${until}T23:59:00` : null })}>{a.save}</button>
        {pick && (
          <button type="button" className="adm-btn ghost" disabled={remove.isPending}
            onClick={() => confirm(a.removeAsk) && remove.mutate(e.id)}>{a.remove}</button>
        )}
        {save.isSuccess && !save.isPending && <span className="pin-note">{a.saved}</span>}
      </div>
      {(save.isError || remove.isError || now.isError) && <p className="pin-err">{a.error}</p>}
    </div>
  );
}

function AdminDoor({ label, title, className, event, side }: { label: string; title?: string; className: string; event?: Picked; side?: Side }) {
  const [open, setOpen] = useState(false);
  if (useMe().data?.role !== "admin") return null;
  return (
    <>
      <button type="button" className={className} title={title} aria-label={title ?? label} onClick={() => setOpen(true)}>{label}</button>
      {open && <SponsorSheet event={event} side={side} onClose={() => setOpen(false)} />}
    </>
  );
}

/** The event page's 💰 (admin only). */
export function SponsorButton({ event }: { event: Picked }) {
  return <AdminDoor label={useAdText().a.btn} className="pin-door" event={event} />;
}

/** A side rail's small ✎ (admin only): its sponsored events and a search to add one on that side. */
export function RailSponsorButton({ side }: { side: Side }) {
  return <AdminDoor label="✎" title={useAdText().a.editSide} className="pin-icon sp-edit" side={side} />;
}
