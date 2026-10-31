// Free entry closing: when a plan's event (or one of its options) still has free tickets with a
// known end (group_free_entry_until), the plan shows when it closes and offers a ready WhatsApp
// reminder for the group and a calendar alarm half an hour before. Under 24 hours left: marked.
import { useEffect, useState } from "react";
import { TZ, useT, when } from "../ui";
import { usePlanText } from "./i18n";
import type { PlanEvent } from "./types";

const DAY_H = 24;

/** When an event's free entry closes, if it still has free tickets and that is ahead. */
export function freeUntil(e: PlanEvent): number | null {
  const at = e.group_free_entry_until ? Date.parse(e.group_free_entry_until) : NaN;
  const free = e.group_has_free_entry ?? e.has_free_entry;
  return free && Number.isFinite(at) && at > Date.now() ? at : null;
}

/** The close time in the venue's own zone: "22:30", and its day when not today ("22:30, Fri"). */
export function freeTime(e: PlanEvent, at: number, t: ReturnType<typeof useT>["t"]) {
  const iso = new Date(at).toISOString();
  const timed = { ...e, time_known: true };
  const clock = when(timed, t, { hour: "2-digit", minute: "2-digit" }, iso);
  const day = (ms: number) => new Date(ms).toLocaleDateString("en-CA", { timeZone: e.time_zone ?? TZ });
  const sameDay = day(at) === day(Date.now());
  return sameDay ? clock : `${clock}, ${when(timed, t, { weekday: "short" }, iso)}`;
}

export function FreeEntry({ events, planUrl }: { events: PlanEvent[]; planUrl: string }) {
  const { t } = useT();
  const p = usePlanText();
  const [, tick] = useState(0);
  useEffect(() => { const id = setInterval(() => tick((n) => n + 1), 60_000); return () => clearInterval(id); }, []); // "x hours left" stays true
  const open = events.map((e) => ({ e, at: freeUntil(e) })).filter((x): x is { e: PlanEvent; at: number } => x.at !== null);
  if (!open.length) return null;
  const utc = (ms: number) => new Date(ms).toISOString().replace(/[-:]|\.\d{3}/g, "");
  return (
    <>
      {open.map(({ e, at }) => {
        const hours = (at - Date.now()) / 3.6e6;
        const time = freeTime(e, at, t);
        const left = p.left(hours);
        const remind = `https://wa.me/?text=${encodeURIComponent(`${p.remindMessage(e.title, time, left)}\n${planUrl}`)}`;
        const cal = "https://calendar.google.com/calendar/render?" + new URLSearchParams({
          action: "TEMPLATE", text: p.calTitle(e.title), details: planUrl, dates: `${utc(at - 30 * 60e3)}/${utc(at)}`,
        });
        return (
          <section key={e.id} className={"plan-free" + (hours < DAY_H ? " soon" : "")} role="status">
            <h2>{p.freeTitle}{hours < DAY_H && <> · <span>{p.freeSoon}</span></>}</h2>
            <p dir="auto">{p.freeUntil(e.title, time, left)}</p>
            <div className="e-actions">
              <a className="f-pill" href={`${e.url}?at=buy`} target="_blank" rel="noopener noreferrer">{p.register}</a>
              <a className="f-pill" href={remind} target="_blank" rel="noopener noreferrer">{p.remind}</a>
              <a className="f-pill" href={cal} target="_blank" rel="noopener noreferrer">{p.remindCal}</a>
            </div>
          </section>
        );
      })}
    </>
  );
}
