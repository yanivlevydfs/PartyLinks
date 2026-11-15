// "Plan the evening" on the map: the open place's happy hour, dinner and late bar stops as small icons.
// Same query (and cache) as the panel's EveningPlan list: no extra request.
import { useQuery } from "@tanstack/react-query";
import { divIcon } from "leaflet";
import { Marker, Tooltip } from "react-leaflet";
import type { EventSummary } from "../types";

interface Evening { steps: { key: string; stops: { event: EventSummary; distance_m: number }[] }[] }

const ICON: Record<string, string> = { drinks: "🥂", dinner: "🍽️", late: "🍻" };

export function EveningPins({ eventId, onOpen }: { eventId: string; onOpen: (e: EventSummary) => void }) {
  const { data } = useQuery({
    queryKey: ["evening", eventId, false],
    queryFn: async () => {
      const res = await fetch(`/events/${encodeURIComponent(eventId)}/evening`);
      if (!res.ok) throw new Error(`${res.status} evening`);
      return (await res.json()) as Evening;
    },
    staleTime: 60_000,
  });
  return <>{data?.steps.flatMap((s) => s.stops.map(({ event: e }) => e.latitude != null && e.longitude != null && (
    <Marker key={`${s.key}:${e.id}`} position={[e.latitude, e.longitude]} zIndexOffset={500}
      icon={divIcon({ className: "eve-pin", html: `<span>${ICON[s.key] ?? "📍"}</span>`, iconSize: [30, 30] })}
      eventHandlers={{ click: () => onOpen(e) }}>
      <Tooltip direction="top" offset={[0, -14]}>{e.title}</Tooltip>
    </Marker>
  )))}</>;
}
