import "leaflet/dist/leaflet.css";
import "maplibre-gl/dist/maplibre-gl.css";
import L, { divIcon } from "leaflet";
import "@maplibre/maplibre-gl-leaflet";
import maplibregl from "maplibre-gl";
import rtlText from "@mapbox/mapbox-gl-rtl-text/mapbox-gl-rtl-text.min.js?url";
import { CircleMarker, MapContainer, Marker, Popup, useMap, useMapEvent } from "react-leaflet";
import { useEffect, useMemo, useRef, useState } from "react";
import Supercluster from "supercluster";
import { Link } from "react-router-dom";
import { toQuery, useCategories, useMapEvents } from "../api";
import { SaveButton } from "../saved/SaveButton";
import { genreLabel } from "../i18n";
import { useAllowed, useConsent } from "../consent";
import { Filters, filterParams, GROUP_ICON } from "../Filters";
import { useStore } from "../store";
import type { MapEvent } from "../types";
import { eventPath, Loader, Sources, useT, useTitle, venuePath, when } from "../ui";
import type { Lang } from "../i18n";

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
  // The home page's filter (same store): the map shows what the list shows, not every event at once.
  // Every date by default: ~17k pins, clustered, ~1 MB gzipped.
  const f = useStore((s) => s.filters);
  const params = filterParams({ ...f, sort: "start_at" });
  const { data, isError, isPending } = useMapEvents(params);
  useTitle(t.nav.map);
  // Tiles come from OpenStreetMap's servers (they see the visitor's IP): only with consent,
  // given in the cookie settings or here, for this visit or for good.
  const allowed = useAllowed("external");
  const [once, setOnce] = useState(false);
  const { consent, save } = useConsent();
  const showTiles = allowed || once;

  // One pin per location; a venue usually hosts many events.
  const spots = new Map<string, MapEvent[]>();
  for (const e of data?.items ?? []) {
    const key = `${e.latitude.toFixed(4)},${e.longitude.toFixed(4)}`;
    spots.set(key, [...(spots.get(key) ?? []), e]);
  }
  const placed = data?.items.length ?? 0;
  const unplaced = data?.unplaced ?? 0;

  return (
    <main className="map-page">
      <div className="wrap"><Cats /><Filters /></div>
      <div className="map-bar wrap">
        <h1 className="sr-only">{t.nav.map}</h1>
        {isError ? t.error : isPending ? <Loader small /> : (
          <>
            <span>{t.onMap(placed)}</span>
            {unplaced > 0 && <small>{t.noLocation(unplaced)}</small>}
          </>
        )}
      </div>
      <div className="wrap map-wrap">
      <MapContainer center={ISRAEL} zoom={8} className="map" scrollWheelZoom>
        {showTiles && <Tiles lang={lang} />}
        <FitTo filter={toQuery(params)} points={[...spots.values()].map((l) => [l[0].latitude, l[0].longitude] as [number, number])} />
        <Pins spots={[...spots.values()]} />
      </MapContainer>
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

const ICON: Record<string, string> = {
  techno: "🎛️", house: "🏠", trance: "🌀", hip_hop: "🎤", rnb: "🎶", mainstream: "🪩", pop: "🎵", reggaeton_latin: "💃",
  afro: "🥁", mizrahi: "🪕", israeli: "🇮🇱", greek: "🏛️", electronic: "⚡", edm: "🔊", throwback: "📼", rock_indie: "🎸",
  jazz: "🎷", live_show: "🎙️", lgbt: "🏳️‍🌈", stand_up: "😂", karaoke: "🎤", hi_tech: "💻", conference: "🧑‍💼", ai: "🤖",
  festival: "🎪", meetups: "🤝", culture: "🎭", exhibitions: "🖼️", workshops: "🛠️", cinema: "🎬", markets: "🛍️", food: "🍽️",
  sport: "⚽", happy_hour: "🥂", theatre: "🎭", kids: "🧸", lectures: "📚", tours: "🗺️", dance: "🩰", yoga_wellness: "🧘",
  escape_rooms: "🔐", attractions: "🎡", coffee_carts: "☕", fine_dining: "🍷", work_spaces: "💼", volunteering: "🤲",
  team_activities: "🧩", nightclubs: "🪩", pools: "🏊", pool_party: "🏖️", nature_party: "🌲", open_bar: "🍸", over_40: "🕺", youth: "🎒",
};

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

/** Nearby spots merge into one numbered bubble (supercluster); a click zooms in until they split. */
function Pins({ spots }: { spots: MapEvent[][] }) {
  const map = useMap();
  const [, redraw] = useState(0);
  useMapEvent("moveend", () => redraw((n) => n + 1));
  const ids = spots.map((l) => l[0].id).join();
  const index = useMemo(() => {
    const sc = new Supercluster<{ list: MapEvent[] }, { n: number }>({
      radius: 60,
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
    if (!("cluster" in c.properties)) return <Spot key={`${lat},${lng}`} list={c.properties.list} />;
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
const PIN = TOUCH
  ? { color: "#ff2e93", fillColor: "#ff2e93", fillOpacity: 0.7, weight: 22, opacity: 0 }
  : { color: "#ff2e93", fillColor: "#ff2e93", fillOpacity: 0.7, weight: 1 };

/** One location and its events. */
function Spot({ list }: { list: MapEvent[] }) {
  const { t } = useT();
  return (
    <CircleMarker
      center={[list[0].latitude, list[0].longitude]}
      // Small pins that grow slowly: big ones merged into one pink blob over the whole country.
      radius={Math.min(5 + Math.log2(list.length) * 2, 14)}
      pathOptions={PIN}
    >
      {/* Narrow enough for a phone, and panned clear of the zoom buttons. */}
      <Popup maxWidth={260} autoPanPaddingTopLeft={[56, 12]} autoPanPaddingBottomRight={[12, 12]}>
        <div className="pop">
          {list[0].venue_name && <Link className="pop-venue" to={venuePath(list[0].venue_name)}>{list[0].venue_name}</Link>}
          <small className="pop-count">{t.results(list.length)}</small>
          <ul>
            {list.slice(0, 8).map((e) => (
              <li key={e.id}>
                <Link to={eventPath(e)}>
                  <small>{when(e, t, { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" })}</small>
                  <span dir="auto">{e.title}</span>
                  <Sources e={e} />
                </Link>
                <SaveButton eventId={e.id} />
              </li>
            ))}
          </ul>
          {list.length > 8 && list[0].venue_name && <Link to={venuePath(list[0].venue_name)}>+{list.length - 8}</Link>}
        </div>
      </Popup>
    </CircleMarker>
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
