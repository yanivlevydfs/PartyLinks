import "leaflet/dist/leaflet.css";
import "maplibre-gl/dist/maplibre-gl.css";
import L, { divIcon } from "leaflet";
import "@maplibre/maplibre-gl-leaflet";
import maplibregl from "maplibre-gl";
import rtlText from "@mapbox/mapbox-gl-rtl-text/mapbox-gl-rtl-text.min.js?url";
import { CircleMarker, MapContainer, Marker, Popup, useMap, useMapEvent } from "react-leaflet";
import { useEffect, useMemo, useRef, useState } from "react";
import Supercluster from "supercluster";
import { Link, useNavigate } from "react-router-dom";
import { toQuery, useCategories, useMapEvents } from "../api";
import { genreLabel } from "../i18n";
import { useAllowed, useConsent } from "../consent";
import { Filters, filterParams, GROUP_ICON } from "../Filters";
import { useStore } from "../store";
import type { MapEvent } from "../types";
import { eventPath, Loader, today, useT, useTitle } from "../ui";
import type { Lang } from "../i18n";
import { GENRE_ICON as ICON } from "../genreIcons";
import { SpotPanel, type PanelMode } from "../map/SpotPanel";
import { MapControls } from "../map/MapControls";
import type { LatLng } from "../map/geo";
import { EveningPins } from "../map/EveningPins";
import { HourBar } from "../map/HourBar";
import { inWindow, isLive, startHour } from "../map/time";
import { linkPath, readLink } from "../map/url";
import { useSaved } from "../saved/useSaved";
import "../map/map.css";

const ISRAEL: [number, number] = [31.9, 34.95];

// Hebrew needs the RTL shaping plugin, or its letters come out reversed. Once per page.
if (maplibregl.getRTLTextPluginStatus() === "unavailable") maplibregl.setRTLTextPlugin(rtlText, true);

/** Keyless OpenFreeMap vector tiles, darkened in CSS (.map .leaflet-tile-pane). Vector, not raster, so
 *  every label is in the site's language (raster OSM writes Lebanon and Syria in Arabic). */
function Tiles({ lang }: { lang: Lang }) {
  const map = useMap();
  useEffect(() => {
    const layer = L.maplibreGL({ style: "https://tiles.openfreemap.org/styles/liberty" }).addTo(map);
    const gl = layer.getMaplibreMap();
    const name = ["coalesce", ["get", `name:${lang}`], ["get", "name_en"], ["get", "name:latin"], ["get", "name"]];
    gl.on("load", () => {
      for (const l of gl.getStyle().layers) {
        if (l.type === "symbol" && gl.getLayoutProperty(l.id, "text-field")) gl.setLayoutProperty(l.id, "text-field", name);
      }
    });
    return () => { layer.remove(); };
  }, [map, lang]);
  return null;
}

export default function MapPage() {
  const { t, lang } = useT();
  const navigate = useNavigate();
  // The home page's filter (same store): the map shows what the list shows, not every event at once.
  // Every date by default: ~17k pins, clustered, ~1 MB gzipped.
  const f = useStore((s) => s.filters);
  const setFilters = useStore((s) => s.setFilters);
  const params = filterParams({ ...f, sort: "start_at" });
  const { data, isError, isPending } = useMapEvents(params);
  useTitle(t.nav.map);
  // Tiles come from OpenStreetMap's servers (they see the visitor's IP): only with consent,
  // given in the cookie settings or here, for this visit or for good.
  const allowed = useAllowed("external");
  const [once, setOnce] = useState(false);
  const { consent, save } = useConsent();
  const showTiles = allowed || once;

  // A shared link (map/url.ts): its filters on arrival, its view instead of the first fit, its place open.
  const link = useRef(readLink()).current;
  useEffect(() => { if (Object.keys(link.filters).length) setFilters(link.filters); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // On top of the filter: my saved events only, and the evening's hour bar.
  const savedIds = useSaved().ids;
  const saved = useMemo(() => new Set(savedIds), [savedIds]);
  const [savedOnly, setSavedOnly] = useState(false);
  const [hour, setHour] = useState<number | null>(null);
  const items = useMemo(
    () => (data?.items ?? []).filter((e) => (!savedOnly || saved.has(e.id)) && (hour == null || inWindow(startHour(e), hour))),
    [data, savedOnly, saved, hour],
  );

  // One pin per location; a venue usually hosts many events.
  const spots = useMemo(() => {
    const out = new Map<string, MapEvent[]>();
    for (const e of items) {
      const key = spotKeyOf(e);
      const list = out.get(key);
      if (list) list.push(e); else out.set(key, [e]);
    }
    return out;
  }, [items]);
  const placed = data?.items.length ?? 0;
  const unplaced = data?.unplaced ?? 0;
  const [heat, setHeat] = useState(false);

  // The side panel: a pin's place, or the list of everything inside the map's view.
  const [spotKey, setSpotKey] = useState<string | null>(link.spot);
  const [listOpen, setListOpen] = useState(false);
  const [bounds, setBounds] = useState<L.LatLngBounds | null>(null);
  const [map, setMap] = useState<L.Map | null>(null);
  const [me, setMe] = useState<LatLng | null>(null);
  // A link's own view, or a visitor who asked for "near me": the map stops fitting itself to the pins.
  const [hold, setHold] = useState(!!link.at);
  const [hover, setHover] = useState<string | null>(null);
  const inView = useMemo(
    () => (bounds ? items.filter((e) => bounds.contains([e.latitude, e.longitude])) : []),
    [bounds, items],
  );
  const spotList = spotKey ? spots.get(spotKey) : undefined;
  const panel: PanelMode | null = spotList ? { kind: "spot", list: spotList } : listOpen ? { kind: "view", list: inView } : null;
  const pick = (key: string) => { setSpotKey(key); setListOpen(false); };
  const focus = (where: LatLng) => map?.flyTo(where, Math.max(map.getZoom(), 16));
  const located = (where: LatLng) => { setMe(where); setHold(true); map?.flyTo(where, 14); };

  // The address bar follows the view, so copying it (or the share button) gives the same map.
  const sharePath = () => (map ? linkPath(map.getCenter(), map.getZoom(), spotKey, f) : location.pathname);
  useEffect(() => { if (map) history.replaceState(history.state, "", sharePath()); }, [bounds, spotKey, f, map]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <main className="map-page">
      <div className="wrap"><Cats /><Filters /></div>
      <div className="map-bar wrap">
        <h1 className="sr-only">{t.nav.map}</h1>
        {isError ? t.error : isPending ? <Loader small /> : (
          <>
            <span>{t.onMap(placed)}</span>
            {unplaced > 0 && <small>{t.noLocation(unplaced)}</small>}
            <button className={"chip map-heat" + (heat ? " on" : "")} aria-pressed={heat} onClick={() => setHeat(!heat)}>{t.heatMap}</button>
          </>
        )}
      </div>
      <div className={"wrap map-wrap" + (panel ? " has-panel" : "")}>
      {/* A phone pinches to zoom: the +/- buttons only covered pins there. */}
      <MapContainer center={link.at ? [link.at[0], link.at[1]] : ISRAEL} zoom={link.at?.[2] ?? 8} className="map" scrollWheelZoom zoomControl={!TOUCH}>
        {showTiles && <Tiles lang={lang} />}
        <Watch onMap={setMap} onBounds={setBounds} onEmptyClick={() => setSpotKey(null)} />
        {!hold && <FitTo filter={toQuery(params)} points={[...spots.values()].map((l) => [l[0].latitude, l[0].longitude] as [number, number])} />}
        {heat ? <Heat items={items} />
          : <Pins spots={[...spots.values()]} selected={spotKey} hover={hover} saved={saved} onPick={pick} />}
        {spotList && !heat && <EveningPins eventId={spotList[0].id} onOpen={(e) => navigate(eventPath(e))} />}
        {me && <CircleMarker center={me} radius={8} pathOptions={{ color: "#fff", weight: 3, fillColor: "#19e3ff", fillOpacity: 1 }} interactive={false} />}
      </MapContainer>
      {/* ~57k pins take a few seconds to arrive: the map says so instead of looking empty. */}
      {isPending && <div className="map-loading"><Loader /><span aria-hidden>{t.loading}</span></div>}
      {data && !heat && (
        <MapControls inView={inView.length} listOpen={listOpen && !spotList}
          onList={() => { setSpotKey(null); setListOpen(!listOpen || !!spotList); }}
          onLocated={located}
          onTonight={() => { setFilters({ when: "tonight" }); setSpotKey(null); setListOpen(true); }}
          savedCount={saved.size} savedOnly={savedOnly} onSavedOnly={() => setSavedOnly(!savedOnly)}
          sharePath={sharePath} />
      )}
      {data && !heat && <HourBar hour={hour} onHour={setHour} count={items.length} />}
      {panel && (
        <SpotPanel mode={panel} me={me} onHover={setHover} onFocus={focus}
          onClose={() => { setSpotKey(null); setListOpen(false); }} />
      )}
      </div>
      {!showTiles && (
        <div className="map-gate" role="region" aria-label={t.mapGate.title}>
          <h2>{t.mapGate.title}</h2>
          <p>{t.mapGate.text} <Link to="/privacy">{t.privacy}</Link></p>
          <div className="consent-btns">
            <button className="c-btn" onClick={() => setOnce(true)}>{t.mapGate.once}</button>
            <button className="c-btn ghost" onClick={() => save({ analytics: false, ...consent?.choices, external: true })}>{t.mapGate.always}</button>
          </div>
        </div>
      )}
    </main>
  );
}

// The heat view: events counted per ~1 km cell (0.01°), a circle per cell, bigger and hotter where more is on.
const HEAT_CELL = 0.01;
const HEAT_MAX_RADIUS = 34;

function Heat({ items }: { items: MapEvent[] }) {
  const { t } = useT();
  const cells = useMemo(() => {
    const out = new Map<string, { lat: number; lng: number; n: number }>();
    for (const e of items) {
      const lat = Math.round(e.latitude / HEAT_CELL) * HEAT_CELL, lng = Math.round(e.longitude / HEAT_CELL) * HEAT_CELL;
      const key = `${lat.toFixed(2)},${lng.toFixed(2)}`;
      const c = out.get(key) ?? { lat, lng, n: 0 };
      c.n += 1;
      out.set(key, c);
    }
    return [...out.values()].sort((a, b) => a.n - b.n); // the hottest drawn last, on top
  }, [items]);
  const most = Math.max(1, ...cells.map((c) => c.n));
  return <>{cells.map((c) => {
    const share = c.n / most;
    return (
      <CircleMarker key={`${c.lat},${c.lng}`} center={[c.lat, c.lng]} radius={6 + Math.sqrt(share) * HEAT_MAX_RADIUS} stroke={false}
        pathOptions={{ fillColor: `hsl(${Math.round(50 - share * 50)} 100% 55%)`, fillOpacity: 0.25 + share * 0.5 }}>
        <Popup>{t.results(c.n)}</Popup>
      </CircleMarker>
    );
  })}</>;
}

/** Big category buttons over the map: one family at a time (the full filter below opens each one and takes several). */
function Cats() {
  const { t, lang } = useT();
  const f = useStore((s) => s.filters);
  const set = useStore((s) => s.setFilters);
  const cats = useCategories().data;
  const groups = [...(cats?.genre_groups ?? [])].sort((a, b) => genreLabel(lang, a.value).localeCompare(genreLabel(lang, b.value), lang));
  if (!groups.length) return null;
  // The families, plus a single category picked below (it shows as one more button).
  const shown = [...groups, ...(cats?.genres ?? []).filter((g) => f.genres.includes(g.value) && !groups.some((x) => x.value === g.value))];
  const one = (g: string) => f.genres.length === 1 && f.genres[0] === g;
  return (
    <div className="map-cats" role="group" aria-label={t.genres}>
      <button className={"map-cat" + (f.genres.length ? "" : " on")} aria-pressed={!f.genres.length} onClick={() => set({ genres: [] })}>
        <span aria-hidden>✨</span><b>{t.all}</b>
      </button>
      {shown.map((g) => (
        <button key={g.value} className={"map-cat" + (f.genres.includes(g.value) ? " on" : "")} aria-pressed={f.genres.includes(g.value)}
          onClick={() => set({ genres: one(g.value) ? [] : [g.value] })}>
          <span aria-hidden>{GROUP_ICON[g.value] ?? ICON[g.value] ?? "🎉"}</span><b>{genreLabel(lang, g.value)}</b><small>{g.events}</small>
        </button>
      ))}
    </div>
  );
}

/** Hands the map and its view up to the page; a click on the map itself (not a pin) closes a place's panel. */
function Watch({ onMap, onBounds, onEmptyClick }: { onMap: (m: L.Map) => void; onBounds: (b: L.LatLngBounds) => void; onEmptyClick: () => void }) {
  const map = useMap();
  useEffect(() => { onMap(map); onBounds(map.getBounds()); }, [map]); // eslint-disable-line react-hooks/exhaustive-deps
  useMapEvent("moveend", () => onBounds(map.getBounds()));
  useMapEvent("click", onEmptyClick);
  return null;
}

const spotKeyOf = (e: MapEvent) => `${e.latitude.toFixed(4)},${e.longitude.toFixed(4)}`;

/** Nearby spots merge into one numbered bubble (supercluster); a click zooms in until they split. */
function Pins({ spots, selected, hover, saved, onPick }: { spots: MapEvent[][]; selected: string | null; hover: string | null; saved: Set<string>; onPick: (key: string) => void }) {
  const map = useMap();
  const [, redraw] = useState(0);
  useMapEvent("moveend", () => redraw((n) => n + 1));
  const ids = spots.map((l) => l[0].id).join();
  const day = today();
  const index = useMemo(() => {
    const sc = new Supercluster<{ list: MapEvent[] }, { n: number }>({
      // Leaflet's tiles are 256px; supercluster's default 512 halved the radius, and bubbles up to 56px overlapped.
      radius: 60,
      extent: 256,
      maxZoom: 17,
      map: (p) => ({ n: p.list.length }),
      reduce: (acc, p) => { acc.n += p.n; },
    });
    sc.load(spots.map((list) => ({
      type: "Feature",
      properties: { list },
      geometry: { type: "Point", coordinates: [list[0].longitude, list[0].latitude] },
    })));
    return sc;
  }, [ids]); // eslint-disable-line react-hooks/exhaustive-deps
  const b = map.getBounds();
  return index.getClusters([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], Math.round(map.getZoom())).map((c) => {
    const [lng, lat] = c.geometry.coordinates;
    // Keyed by place, not by its first event: a refetch reorders/changes the list, and a new key
    // remounted the pin and closed its open popup.
    if (!("cluster" in c.properties)) {
      const list = c.properties.list, key = spotKeyOf(list[0]);
      const now = Date.now();
      const kind: Kind = list.some((e) => saved.has(e.id)) ? "saved" : list.some((e) => isLive(e, now)) ? "live" : list.some((e) => e.local_date === day) ? "today" : "later";
      return <Spot key={`${lat},${lng}`} list={list} kind={kind}
        selected={selected === key} hot={hover === `${list[0].latitude},${list[0].longitude}`} onPick={() => onPick(key)} />;
    }
    const { cluster_id: id, n } = c.properties;
    const size = Math.round(Math.min(30 + Math.log2(n) * 5, 56));
    return (
      <Marker
        key={`c${id}`}
        position={[lat, lng]}
        icon={divIcon({ className: "cluster", html: `<span>${n}</span>`, iconSize: [size, size] })}
        // At least two levels deeper per click: the split zoom alone was sometimes the current one (the
        // map's zoom is fractional), so a click did nothing and Tel Aviv took ~14 clicks to reach a pin.
        eventHandlers={{ click: () => map.setView([lat, lng], Math.min(18, Math.max(index.getClusterExpansionZoom(id), Math.floor(map.getZoom()) + 2))) }}
      />
    );
  });
}

// A finger needs a bigger target than a 5px dot: on touch screens a wide, invisible stroke takes the tap
// (a miss landed on the map, which closed the open popup instead of opening the next one).
const TOUCH = window.matchMedia("(pointer: coarse)").matches;
// Gold: one of my saved events; green (pulsing): on right now; cyan: on today; pink: later.
// A white ring: the open place, or the list row under the mouse.
const COLOR = { saved: "#ffc531", live: "#2bf5a0", today: "#19e3ff", later: "#ff2e93" } as const;
type Kind = keyof typeof COLOR;

/** One location; a click opens its events in the side panel. */
function Spot({ list, kind, selected, hot, onPick }: { list: MapEvent[]; kind: Kind; selected: boolean; hot: boolean; onPick: () => void }) {
  const fill = COLOR[kind];
  const ring = selected || hot;
  // Small pins that grow slowly: big ones merged into one pink blob over the whole country.
  const radius = Math.min(5 + Math.log2(list.length) * 2, 14) + (ring ? 4 : 0);
  return (
    <CircleMarker
      center={[list[0].latitude, list[0].longitude]}
      radius={radius}
      // A finger needs a bigger target than a 5px dot: a wide invisible stroke takes the tap.
      pathOptions={{
        ...(ring ? { color: "#fff", weight: 3, opacity: 1, fillColor: fill, fillOpacity: 0.95 }
          : TOUCH ? { color: fill, fillColor: fill, fillOpacity: 0.8, weight: 22, opacity: 0 }
          : { color: fill, fillColor: fill, fillOpacity: 0.8, weight: 1 }),
        className: kind === "live" ? "pin-live" : undefined,
      }}
      eventHandlers={{ click: (ev) => { L.DomEvent.stopPropagation(ev); onPick(); } }}
    />
  );
}

const near = ([la, ln]: [number, number], [cla, cln]: [number, number], deg: number) =>
  Math.abs(la - cla) < deg && Math.abs(ln - cln) < deg;
const TEL_AVIV: [number, number] = [32.08, 34.78];

/** Zoom to the pins when the filter changes. Only then: a background refetch (tab back in focus,
 *  new events scraped) used to refit the map, jumping away and closing the open popup.
 *  A phone fits the Tel Aviv pins first (else Israel's), so a few abroad don't zoom it out to Europe. */
function FitTo({ filter, points }: { filter: string; points: [number, number][] }) {
  const map = useMap();
  const fitted = useRef<string | null>(null);
  useEffect(() => {
    if (!points.length || fitted.current === filter) return;
    fitted.current = filter;
    let fit = points;
    if (window.matchMedia("(max-width: 760px)").matches) {
      const tlv = points.filter((p) => near(p, TEL_AVIV, 0.2));
      const il = points.filter((p) => near(p, [31.4, 35], 2));
      fit = tlv.length ? tlv : il.length ? il : points;
    }
    map.fitBounds(fit, { padding: [30, 30], maxZoom: 15 });
  }, [filter, points.length]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}
