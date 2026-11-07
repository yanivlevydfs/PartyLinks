// /holidays: the coming Israeli holidays (GET /holidays/list, partylinks.holiday_pages), each with its dates and
// how many events are on; one with enough events opens its own page (/holiday/<key>). Crawlers get the same
// list from holiday_pages/pages.py.
import { Link } from "react-router-dom";
import { useHolidays } from "../api";
import type { Lang } from "../i18n";
import { Hero, Loader, TZ, useT, useTitle } from "../ui";

const TEXT: Record<Lang, { title: string; intro: string; events: (n: number) => string }> = {
  he: { title: "חגים", intro: "מסיבות, הופעות ואירועים לכל חגי ישראל, מערב החג ועד סופו.", events: (n) => `${n} אירועים` },
  en: { title: "Holidays", intro: "Parties, concerts and events for every Israeli holiday, from the eve to its last day.", events: (n) => `${n} events` },
};

export default function HolidaysPage() {
  const { t, lang } = useT();
  const p = TEXT[lang];
  useTitle(p.title);
  const holidays = useHolidays().data?.holidays;
  const day = (d: string) => new Date(d + "T12:00:00Z").toLocaleDateString(t.locale, { timeZone: TZ, day: "numeric", month: "numeric" });
  return (
    <main>
      <Hero title={p.title}><p>{p.intro}</p></Hero>
      <div className="wrap">
        {!holidays ? <Loader /> : (
          <div className="map-cats places-cats">
            {holidays.filter((h) => h.page).map((h) => (
              <Link key={h.key} className="map-cat" to={`/holiday/${h.key}`}>
                <span aria-hidden>✡️</span>
                <b>{h[lang]}</b>
                <small>{day(h.start)}{h.end !== h.start ? `–${day(h.end)}` : ""} · {p.events(h.events)}</small>
              </Link>
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
