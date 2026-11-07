// /top: this week's most wanted events (saves, page views, ticket sites selling them) and the rising genres
// (GET /top/list, partylinks.weekly_top). Crawlers get the same list from weekly_top/pages.py.
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { genreLabel, type Lang } from "../i18n";
import type { EventSummary } from "../types";
import { Card, GridLoading, Hero, useT, useTitle } from "../ui";

const TEXT: Record<Lang, { title: string; intro: string; rising: string; isNew: (n: number) => string }> = {
  he: { title: "🔥 הכי מבוקשים השבוע", intro: "לפי מה ששומרים, מה שצופים בו, וכמה אתרי כרטיסים מוכרים אותו.", rising: "📈 הז'אנרים העולים", isNew: (n) => `${n} חדשים` },
  en: { title: "🔥 Most wanted this week", intro: "By what people save, what they look at, and how many ticketing sites sell it.", rising: "📈 Rising genres", isNew: (n) => `${n} new` },
};

export default function TopPage() {
  const { lang } = useT();
  const p = TEXT[lang];
  useTitle(p.title);
  const { data } = useQuery({
    queryKey: ["top"],
    queryFn: async () => {
      const res = await fetch("/top/list");
      if (!res.ok) throw new Error(`${res.status} top`);
      return (await res.json()) as { events: EventSummary[]; rising: { genre: string; new: number }[] };
    },
    staleTime: 5 * 60_000,
  });
  return (
    <main>
      <Hero title={p.title}><p>{p.intro}</p></Hero>
      <div className="wrap">
        {!data ? <GridLoading /> : (
          <>
            <div className="grid">{data.events.map((e) => <Card key={e.id} e={e} />)}</div>
            {data.rising.length > 0 && (
              <section className="similar">
                <h4 className="section-title">{p.rising}</h4>
                <div className="map-cats">
                  {data.rising.map((r) => (
                    <Link key={r.genre} className="map-cat" to={`/genre/${r.genre}`}>
                      <b>{genreLabel(lang, r.genre)}</b><small>{p.isNew(r.new)}</small>
                    </Link>
                  ))}
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </main>
  );
}
