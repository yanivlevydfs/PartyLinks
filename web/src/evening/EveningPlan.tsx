// "Plan the evening" on an event's page (GET /events/{id}/evening, partylinks.evening): places near the event
// open before it (drinks, dinner) and after it (coffee the morning after), nearest first, and what else is on
// at the same place this week. Steps and distances come from the server's `evening` settings.
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import type { EventSummary } from "../types";
import { eventPath, isOn, useT, when } from "../ui";
import type { Lang } from "../i18n";

interface Evening {
  steps: { key: string; stops: { event: EventSummary; distance_m: number }[] }[];
  venue: EventSummary[];
}

const TEXT: Record<Lang, { title: string; venue: string; steps: Record<string, string>; m: (n: number) => string }> = {
  he: {
    title: "🗺️ תכננו את הערב",
    venue: "📅 השבוע במקום",
    steps: { drinks: "🥂 לפני: האפי האוור קרוב", dinner: "🍽️ לפני: ארוחה טובה", morning: "☕ אחרי: קפה של בוקר" },
    m: (n) => (n < 1000 ? `${n} מ׳` : `${(n / 1000).toFixed(1)} ק״מ`),
  },
  en: {
    title: "🗺️ Plan the evening",
    venue: "📅 This week at the place",
    steps: { drinks: "🥂 Before: a happy hour nearby", dinner: "🍽️ Before: a good dinner", morning: "☕ After: morning coffee" },
    m: (n) => (n < 1000 ? `${n} m` : `${(n / 1000).toFixed(1)} km`),
  },
};

function Stop({ e, distance }: { e: EventSummary; distance?: string }) {
  const { t } = useT();
  const start = when(e, t, { weekday: "short", hour: "2-digit", minute: "2-digit" });
  const end = e.end_at ? when(e, t, { hour: "2-digit", minute: "2-digit" }, e.end_at) : "";
  return (
    <li>
      <Link to={eventPath(e)}>
        <b dir="auto">{e.title}</b>
        <small>
          {[start && (end ? `${start}–${end}` : start), distance, isOn(e) && (e.place ? t.now : t.happeningNow)].filter(Boolean).join(" · ")}
        </small>
        {e.deal && <small className="deal" dir="auto">🥂 {e.deal}</small>}
      </Link>
    </li>
  );
}

export function EveningPlan({ eventId }: { eventId: string }) {
  const { lang } = useT();
  const p = TEXT[lang];
  const { data } = useQuery({
    queryKey: ["evening", eventId],
    queryFn: async () => {
      const res = await fetch(`/events/${encodeURIComponent(eventId)}/evening`);
      if (!res.ok) throw new Error(`${res.status} evening`);
      return (await res.json()) as Evening;
    },
    staleTime: 60_000,
  });
  if (!data || (!data.steps.length && !data.venue.length)) return null;
  return (
    <>
      {data.steps.length > 0 && (
        <section className="evening">
          <h4>{p.title}</h4>
          {data.steps.map((s) => (
            <div key={s.key} className="evening-step">
              <h5>{p.steps[s.key] ?? s.key}</h5>
              <ul>{s.stops.map((x) => <Stop key={x.event.id} e={x.event} distance={p.m(x.distance_m)} />)}</ul>
            </div>
          ))}
        </section>
      )}
      {data.venue.length > 0 && (
        <section className="evening">
          <h4>{p.venue}</h4>
          <ul>{data.venue.map((e) => <Stop key={e.id} e={e} />)}</ul>
        </section>
      )}
    </>
  );
}
