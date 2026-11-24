// "Plan the evening" on an event's page (GET /events/{id}/evening, partylinks.evening): places near the event
// open before it (drinks, dinner) and after it (a bar or late food), nearest first, and what else is on
// at the same place this week. Steps and distances come from the server's `evening` settings.
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import type { EventSummary } from "../types";
import { eventPath, Img, isOn, sized, useT, when } from "../ui";
import type { Lang } from "../i18n";

interface Evening {
  steps: { key: string; stops: { event: EventSummary; distance_m: number }[] }[];
  venue: EventSummary[];
  budget_max_level: number;
}

const TEXT: Record<Lang, { title: string; venue: string; places: (n: number) => string; budget: (cap: string) => string; steps: Record<string, string>; m: (n: number) => string }> = {
  he: {
    title: "🗺️ תכננו את הערב",
    places: (n) => `${n} מקומות ליד: לפני ואחרי`,
    budget: (cap) => `💸 רק מקומות זולים (עד ${cap})`,
    venue: "📅 השבוע במקום",
    steps: { drinks: "🥂 לפני: האפי האוור קרוב", dinner: "🍽️ לפני: ארוחה טובה", late: "🍻 אחרי: בר או אוכל בלילה" },
    m: (n) => (n < 1000 ? `${n} מ׳` : `${(n / 1000).toFixed(1)} ק״מ`),
  },
  en: {
    title: "🗺️ Plan the evening",
    places: (n) => `${n} places nearby: before and after`,
    budget: (cap) => `💸 Cheap places only (up to ${cap})`,
    venue: "📅 This week at the place",
    steps: { drinks: "🥂 Before: a happy hour nearby", dinner: "🍽️ Before: a good dinner", late: "🍻 After: a bar or late food" },
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
          {[start && (end ? `${start}–${end}` : start), distance, !!e.price_level && "₪".repeat(e.price_level), !!e.rating && `★ ${e.rating}`,
            isOn(e) && (e.place ? t.now : e.genres?.includes("online") ? t.onlineNow : t.happeningNow)].filter(Boolean).join(" · ")}
        </small>
        {e.deal && <small className="deal" dir="auto">🥂 {e.deal}</small>}
      </Link>
    </li>
  );
}

/** Another event at the same place this week: a small poster card. */
function VenueCard({ e }: { e: EventSummary }) {
  const { t } = useT();
  return (
    <Link className="ev-card" to={eventPath(e)}>
      <Img src={sized(e.image_url ?? e.cover_url, 640)} alt="" className="ev-img" text={e.title} />
      <small>{when(e, t, { weekday: "short", day: "numeric", month: "numeric" })}</small>
      <b dir="auto">{e.title}</b>
    </Link>
  );
}

export function EveningPlan({ eventId }: { eventId: string }) {
  const { lang } = useT();
  const p = TEXT[lang];
  const [budget, setBudget] = useState(false);
  const [open, setOpen] = useState(false);
  const { data } = useQuery({
    queryKey: ["evening", eventId, budget],
    queryFn: async () => {
      const res = await fetch(`/events/${encodeURIComponent(eventId)}/evening${budget ? "?budget=true" : ""}`);
      if (!res.ok) throw new Error(`${res.status} evening`);
      return (await res.json()) as Evening;
    },
    staleTime: 60_000,
    placeholderData: (prev) => prev, // the toggle keeps the plan on screen while the other one loads
  });
  if (!data || (!data.steps.length && !data.venue.length && !budget)) return null;
  // Off, the toggle is offered only when a stop costs more than the cap: otherwise it would change nothing.
  const pricey = data.steps.some((s) => s.stops.some((x) => (x.event.price_level ?? 0) > data.budget_max_level));
  const stops = data.steps.reduce((n, s) => n + s.stops.length, 0);
  return (
    <>
      {(data.steps.length > 0 || budget) && (
        <section className="evening">
          {/* Folded to one line: the places are a side plan, not the event. */}
          <button type="button" className="ev-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
            <b>{p.title}</b>
            <small>{p.places(stops)}</small>
            <span aria-hidden>{open ? "▴" : "▾"}</span>
          </button>
          {open && (budget || pricey) && (
            <button className={"chip toggle" + (budget ? " on" : "")} aria-pressed={budget} onClick={() => setBudget(!budget)}>
              <span className="tick" aria-hidden /> {p.budget("₪".repeat(data.budget_max_level))}
            </button>
          )}
          {open && data.steps.map((s) => (
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
          <div className="ev-cards">{data.venue.map((e) => <VenueCard key={e.id} e={e} />)}</div>
        </section>
      )}
    </>
  );
}
