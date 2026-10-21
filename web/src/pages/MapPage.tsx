import "leaflet/dist/leaflet.css";
import { CircleMarker, MapContainer, Popup, TileLayer, useMap } from "react-leaflet";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAllEvents } from "../api";
import { useAllowed, useConsent } from "../consent";
import { Filters, filterParams } from "../Filters";
import { useStore } from "../store";
import type { EventSummary } from "../types";
import { eventPath, fmtDate, Sources, useT, useTitle, venuePath } from "../ui";

const ISRAEL: [number, number] = [31.9, 34.95];

export default function MapPage() {
  const { t } = useT();
  // The home page's filter (same store): the map shows what the list shows, not every event at once.
  const f = useStore((s) => s.filters);
  const set = useStore((s) => s.setFilters);
  // Every date at once is thousands of pins (and a slow load): the map opens on tonight.
  useEffect(() => {
    if (useStore.getState().filters.when === "all") set({ when: "tonight" });
  }, [set]);
  const { data, isError, isPending } = useAllEvents(filterParams({ ...f, sort: "start_at" }));
  useTitle(t.nav.map);
  // Tiles come from OpenStreetMap's servers (they see the visitor's IP): only with consent,
  // given in the cookie settings or here, for this visit or for good.
  const allowed = useAllowed("external");
  const [once, setOnce] = useState(false);
  const { consent, save } = useConsent();
  const showTiles = allowed || once;

  // One pin per location; a venue usually hosts many events.
  const spots = new Map<string, EventSummary[]>();
  for (const e of data ?? []) {
    if (e.latitude == null || e.longitude == null) continue;
    const key = `${e.latitude.toFixed(4)},${e.longitude.toFixed(4)}`;
    spots.set(key, [...(spots.get(key) ?? []), e]);
  }
  const placed = [...spots.values()].reduce((n, list) => n + list.length, 0);
  const unplaced = (data?.length ?? 0) - placed;

  return (
    <main className="map-page">
      <div className="wrap"><Filters /></div>
      <div className="map-bar wrap">
        <h1 className="sr-only">{t.nav.map}</h1>
        {isError ? t.error : isPending ? t.loading : (
          <>
            <span>{t.onMap(placed)}</span>
            {unplaced > 0 && <small>{t.noLocation(unplaced)}</small>}
          </>
        )}
      </div>
      <MapContainer center={ISRAEL} zoom={8} className="map" scrollWheelZoom>
        {/* Keyless OSM tiles, darkened in CSS (.map .leaflet-tile-pane). */}
        {showTiles && (
          <TileLayer
            url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
            maxZoom={19}
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          />
        )}
        <FitTo points={[...spots.values()].map((l) => [l[0].latitude!, l[0].longitude!] as [number, number])} />
        {[...spots.entries()].map(([key, list]) => (
          <CircleMarker
            key={key}
            center={[list[0].latitude!, list[0].longitude!]}
            // Small pins that grow slowly: big ones merged into one pink blob over the whole country.
            radius={Math.min(5 + Math.log2(list.length) * 2, 14)}
            pathOptions={{ color: "#ff2e93", fillColor: "#ff2e93", fillOpacity: 0.7, weight: 1 }}
          >
            <Popup>
              <div className="pop">
                {list[0].venue_name && <Link className="pop-venue" to={venuePath(list[0].venue_name)}>{list[0].venue_name}</Link>}
                <small className="pop-count">{t.results(list.length)}</small>
                <ul>
                  {list.slice(0, 8).map((e) => (
                    <li key={e.id}>
                      <Link to={eventPath(e)}>
                        <small>{fmtDate(e.start_at, t, { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" })}</small>
                        <span dir="auto">{e.title}</span>
                        <Sources e={e} />
                      </Link>
                    </li>
                  ))}
                </ul>
                {list.length > 8 && list[0].venue_name && <Link to={venuePath(list[0].venue_name)}>+{list.length - 8}</Link>}
              </div>
            </Popup>
          </CircleMarker>
        ))}
      </MapContainer>
      {!showTiles && (
        <div className="map-gate" role="region" aria-label={t.mapGate.title}>
          <h2>{t.mapGate.title}</h2>
          <p>{t.mapGate.text} <Link to="/privacy">{t.privacy}</Link></p>
          <div className="consent-btns">
            <button className="c-btn" onClick={() => setOnce(true)}>{t.mapGate.once}</button>
            <button className="c-btn ghost" onClick={() => save({ ...consent?.choices, external: true })}>{t.mapGate.always}</button>
          </div>
        </div>
      )}
    </main>
  );
}

/** Zoom to the pins whenever the filter changes what is shown. */
function FitTo({ points }: { points: [number, number][] }) {
  const map = useMap();
  const key = points.map((p) => p.join()).sort().join("|");
  useEffect(() => {
    if (points.length) map.fitBounds(points, { padding: [30, 30], maxZoom: 15 });
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}
