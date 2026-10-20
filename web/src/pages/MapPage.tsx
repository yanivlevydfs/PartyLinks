import "leaflet/dist/leaflet.css";
import { CircleMarker, MapContainer, Popup, TileLayer } from "react-leaflet";
import { Link } from "react-router-dom";
import { useAllEvents } from "../api";
import type { EventSummary } from "../types";
import { eventPath, fmtDate, Sources, useT, useTitle, venuePath } from "../ui";

const TEL_AVIV: [number, number] = [32.0809, 34.7806];

export default function MapPage() {
  const { t } = useT();
  const { data, isError, isPending } = useAllEvents();
  useTitle(t.nav.map);

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
      <div className="map-bar wrap">
        {isError ? t.error : isPending ? t.loading : (
          <>
            <span>{t.onMap(placed)}</span>
            {unplaced > 0 && <small>{t.noLocation(unplaced)}</small>}
          </>
        )}
      </div>
      <MapContainer center={TEL_AVIV} zoom={13} className="map" scrollWheelZoom>
        {/* Keyless OSM tiles, darkened in CSS (.map .leaflet-tile-pane). */}
        <TileLayer
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
          maxZoom={19}
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        />
        {[...spots.entries()].map(([key, list]) => (
          <CircleMarker
            key={key}
            center={[list[0].latitude!, list[0].longitude!]}
            radius={Math.min(8 + list.length * 1.5, 24)}
            pathOptions={{ color: "#ff2e93", fillColor: "#ff2e93", fillOpacity: 0.55, weight: 2 }}
          >
            <Popup>
              <div className="pop">
                {list[0].venue_name && <Link className="pop-venue" to={venuePath(list[0].venue_name)}>{list[0].venue_name}</Link>}
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
    </main>
  );
}
