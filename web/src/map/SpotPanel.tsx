// The map's side panel (a bottom sheet on phones): one place's events, or every event in the map's view.
// Rows start from the map's light pins and fill in from GET /events/cards (picture, price, genres), a page at a time
// (site.web.map_popup_events, admin config): a panel never asks for more cards than it shows.
import { useQueries } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { SaveButton } from "../saved/SaveButton";
import { genreLabel } from "../i18n";
import { GENRE_ICON } from "../genreIcons";
import type { EventSummary, MapEvent } from "../types";
import { eventPath, Img, isOn, priceLabel, ShareButton, sized, Sources, useT, venuePath, when } from "../ui";
import { isLive } from "./time";
import { dayTest, googleUrl, km, wazeUrl, type Day, type LatLng } from "./geo";
import { useMapText } from "./i18n";
import { Weather, forecastAt } from "../Weather";
import { EveningPlan } from "../evening/EveningPlan";
import { SaveAlertButton } from "../alerts/SaveAlertButton";
import { useStore } from "../store";
import { useWeb } from "../site_params/useWeb";

export type PanelMode = { kind: "spot"; list: MapEvent[] } | { kind: "view"; list: MapEvent[] };

const at = (e: MapEvent): LatLng => [e.latitude, e.longitude];

function useCards(ids: string[], PAGE: number): Map<string, EventSummary> {
  const chunks: string[][] = [];
  for (let i = 0; i < ids.length; i += PAGE) chunks.push(ids.slice(i, i + PAGE));
  const results = useQueries({
    queries: chunks.map((chunk) => ({
      queryKey: ["map-cards", chunk],
      queryFn: () =>
        fetch(`/events/cards?${chunk.map((i) => `ids=${encodeURIComponent(i)}`).join("&")}`)
          .then((r) => (r.ok ? r.json() : { items: [] })) as Promise<{ items: EventSummary[] }>,
      staleTime: 5 * 60_000,
    })),
  });
  const out = new Map<string, EventSummary>();
  for (const r of results) for (const c of r.data?.items ?? []) out.set(c.id, c);
  return out;
}

export function SpotPanel({ mode, me, onClose, onHover, onFocus }: {
  mode: PanelMode;
  me: LatLng | null;
  onClose: () => void;
  onHover: (key: string | null) => void;
  onFocus: (where: LatLng) => void;
}) {
  const m = useMapText();
  const PAGE = useWeb().map_popup_events;
  const [day, setDay] = useState<Day>("all");
  const [byDistance, setByDistance] = useState(false);
  const [shown, setShown] = useState(PAGE);
  const [expanded, setExpanded] = useState(false);
  const body = useRef<HTMLDivElement>(null);
  const spot = mode.kind === "spot" ? mode.list[0] : null;
  const spotKey = spot ? `${spot.latitude},${spot.longitude}` : "view";

  // A new place starts from the top, every day, the first page.
  useEffect(() => { setDay("all"); setShown(PAGE); body.current?.scrollTo(0, 0); }, [spotKey]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (me && mode.kind === "view") setByDistance(true); }, [me, mode.kind]);

  const counts = useMemo(() => {
    const c: Record<Day, number> = { all: 0, today: 0, tomorrow: 0, weekend: 0 };
    for (const d of ["all", "today", "tomorrow", "weekend"] as Day[]) {
      const test = dayTest(d);
      c[d] = mode.list.filter((e) => test(e.local_date)).length;
    }
    return c;
  }, [mode.list]);

  const rows = useMemo(() => {
    const test = dayTest(day);
    const list = mode.list.filter((e) => test(e.local_date));
    return byDistance && me ? [...list].sort((a, b) => km(me, at(a)) - km(me, at(b))) : list;
  }, [mode.list, day, byDistance, me]);

  const visible = rows.slice(0, shown);
  const cards = useCards(visible.map((e) => e.id), PAGE);
  const first = spot ? cards.get(spot.id) : undefined;
  const place = first ? [first.address, first.city].filter(Boolean).join(", ") : "";
  const filters = useStore((s) => s.filters);

  return (
    <aside className={"spot-panel" + (expanded ? " expanded" : "")} aria-label={spot?.venue_name ?? m.inView(mode.list.length)}>
      <button className="mp-handle" onClick={() => setExpanded(!expanded)} aria-label={m.expand} aria-expanded={expanded} />
      <header className="mp-head">
        <div className="mp-title">
          {spot ? (
            <>
              {spot.venue_name ? <Link className="mp-venue" to={venuePath(spot.venue_name)}>{spot.venue_name}</Link>
                : <b className="mp-venue">{m.events(mode.list.length)}</b>}
              <small>
                {[place, m.events(mode.list.length), me && m.km(km(me, at(spot)))].filter(Boolean).join(" · ")}
              </small>
            </>
          ) : (
            <b className="mp-venue">{m.inView(mode.list.length)}</b>
          )}
        </div>
        <button className="mp-x" onClick={onClose} aria-label={m.close}>✕</button>
      </header>

      {spot && (
        <div className="mp-actions">
          <a className="mp-act" href={wazeUrl(at(spot))} target="_blank" rel="noopener noreferrer">🚗 {m.waze}</a>
          <a className="mp-act" href={googleUrl(at(spot))} target="_blank" rel="noopener noreferrer">🗺️ {m.google}</a>
          {spot.venue_name && <ShareButton path={venuePath(spot.venue_name)} title={spot.venue_name} />}
        </div>
      )}

      <div className="mp-tabs" role="tablist">
        {(["all", "today", "tomorrow", "weekend"] as Day[]).map((d) => (
          <button key={d} role="tab" aria-selected={day === d} className={"mp-tab" + (day === d ? " on" : "")}
            disabled={!counts[d]} onClick={() => { setDay(d); setShown(PAGE); }}>
            {m[d]} <small>{counts[d]}</small>
          </button>
        ))}
        {me && mode.kind === "view" && (
          <button className="mp-tab sort" onClick={() => setByDistance(!byDistance)}>
            ⇅ {byDistance ? m.byDistance : m.byDate}
          </button>
        )}
      </div>

      <div className="mp-body" ref={body}>
        {!rows.length && <p className="mp-empty">{m.nothing}</p>}
        <ul className="mp-list">
          {visible.map((e) => (
            <Row key={e.id} e={e} card={cards.get(e.id)} dist={me && !spot ? km(me, at(e)) : null}
              onHover={onHover} onFocus={mode.kind === "view" ? onFocus : undefined} />
          ))}
        </ul>
        {rows.length > shown && (
          <button className="mp-more" onClick={() => setShown(shown + PAGE)}>{m.more} ({rows.length - shown})</button>
        )}
        {/* Around the place: drinks / dinner before, a bar after (the event page's "plan the evening"), for its next event. */}
        {spot && <div className="mp-evening"><EveningPlan eventId={spot.id} /></div>}
        {spot && first?.city && (
          <div className="mp-alert"><SaveAlertButton filters={{ ...filters, city: first.city, regions: [], areas: [] }} label={m.alertHere(first.city)} /></div>
        )}
      </div>
    </aside>
  );
}

function Row({ e, card, dist, onHover, onFocus }: {
  e: MapEvent;
  card: EventSummary | undefined;
  dist: number | null;
  onHover: (key: string | null) => void;
  onFocus?: (where: LatLng) => void;
}) {
  const { t, lang } = useT();
  const m = useMapText();
  const price = card && priceLabel({
    ...card,
    min_price: card.group_min_price ?? card.min_price,
    has_free_entry: card.group_has_free_entry ?? card.has_free_entry,
    entry_type: card.group_entry_type ?? card.entry_type,
  }, t);
  const genre = card?.genres[0];
  const live = isLive(e) || (card && isOn(card));
  const key = `${e.latitude},${e.longitude}`;
  return (
    <li className="mp-row" onMouseEnter={() => onHover(key)} onMouseLeave={() => onHover(null)}>
      <Link className="mp-link" to={eventPath(e)}>
        {card ? <Img src={sized(card.cover_url ?? card.image_url, 640)} alt="" className="mp-img" text={e.title} />
          : <div className="mp-img mp-ghost" aria-hidden />}
        <div className="mp-info">
          <small className="mp-when">
            {live && <span className="mp-live">● {m.now}</span>}
            {when(e, t, { weekday: "short", day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" })}
            {/* Every event with a place in the coming days shows its forecast (one request per ~10 km cell). */}
            <Weather lat={e.latitude} lng={e.longitude} city={card?.city} start={forecastAt(card ?? e)} zone={e.time_zone} />
          </small>
          <b dir="auto">{e.title}</b>
          <span className="mp-meta">
            {genre && <span>{GENRE_ICON[genre] ?? "🎉"} {genreLabel(lang, genre)}</span>}
            {e.venue_name && onFocus && <span dir="auto">📍 {e.venue_name}</span>}
            {dist != null && <span>{m.km(dist)}</span>}
          </span>
          <Sources e={e} />
        </div>
      </Link>
      <div className="mp-side">
        {card?.is_sold_out ? <span className="mp-price sold">{m.soldOut}</span>
          : price && <span className={"mp-price" + (price === t.free ? " free" : "")}>{price}</span>}
        <div className="mp-btns">
          {onFocus && <button className="mp-pin" onClick={() => onFocus(at(e))} aria-label="📍">📍</button>}
          <SaveButton eventId={e.id} />
        </div>
        {card?.url && !card.is_sold_out && (
          <a className="mp-buy" href={`${card.url}?at=map`} target="_blank" rel="noopener noreferrer">{m.tickets} ↗</a>
        )}
      </div>
    </li>
  );
}
