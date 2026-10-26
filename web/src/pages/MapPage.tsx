import "leaflet/dist/leaflet.css";
import { divIcon } from "leaflet";
import { CircleMarker, MapContainer, Marker, Popup, TileLayer, useMap, useMapEvent } from "react-leaflet";
import { useEffect, useMemo, useState } from "react";
import Supercluster from "supercluster";
import { Link } from "react-router-dom";
import { useMapEvents } from "../api";
import { Filters, filterParams } from "../Filters";
import { useStore } from "../store";
import type { MapEvent } from "../types";
import { eventPath, fmtDate, Loader, Sources, useT, useTitle, venuePath } from "../ui";

const ISRAEL: [number, number] = [31.9, 34.95];

export default function MapPage() {
  const { t } = useT();
  // The home page's filter (same store): the map shows what the list shows, not every event at once.
  // Every date by default: ~17k pins, clustered, ~1 MB gzipped.
  const f = useStore((s) => s.filters);
  const { data, isError, isPending } = useMapEvents(filterParams({ ...f, sort: "start_at" }));
  useTitle(t.nav.map);

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
      <div className="wrap"><Filters /></div>
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
        {/* Keyless OSM tiles, darkened in CSS (.map .leaflet-tile-pane). */}
        <TileLayer
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
          maxZoom={19}
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        />
        <FitTo points={[...spots.values()].map((l) => [l[0].latitude, l[0].longitude] as [number, number])} />
        <Pins spots={[...spots.values()]} />
      </MapContainer>
      </div>
    </main>
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
    if (!("cluster" in c.properties)) return <Spot key={c.properties.list[0].id} list={c.properties.list} />;
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

/** One location and its events. */
function Spot({ list }: { list: MapEvent[] }) {
  const { t } = useT();
  return (
    <CircleMarker
      center={[list[0].latitude, list[0].longitude]}
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
  );
}

const near = ([la, ln]: [number, number], [cla, cln]: [number, number], deg: number) =>
  Math.abs(la - cla) < deg && Math.abs(ln - cln) < deg;
const TEL_AVIV: [number, number] = [32.08, 34.78];

/** Zoom to the pins whenever the filter changes what is shown. A phone fits the
 *  Tel Aviv pins first (else Israel's), so a few abroad don't zoom it out to Europe. */
function FitTo({ points }: { points: [number, number][] }) {
  const map = useMap();
  const key = points.map((p) => p.join()).sort().join("|");
  useEffect(() => {
    let fit = points;
    if (window.matchMedia("(max-width: 760px)").matches) {
      const tlv = points.filter((p) => near(p, TEL_AVIV, 0.2));
      const il = points.filter((p) => near(p, [31.4, 35], 2));
      fit = tlv.length ? tlv : il.length ? il : points;
    }
    if (fit.length) map.fitBounds(fit, { padding: [30, 30], maxZoom: 15 });
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}
