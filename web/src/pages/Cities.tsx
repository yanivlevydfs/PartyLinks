// /cities: every city with a page, by country: Israel's cities (/city/<key>) first, then each country
// abroad (/abroad/<country>) with its cities (/abroad/<country>/<city>). GET /cities-index, the same
// list the server renders for crawlers (seo.cities_page).
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Hero, Loader, useT, useTitle } from "../ui";

interface CityLink { path: string; name: string; events: number }
interface Country extends CityLink { code: string; cities: CityLink[] }

const TEXT = {
  he: {
    title: "ערים", intro: "כל הערים עם אירועים קרובים, לפי מדינה: ערי ישראל וערים בחו״ל.",
    all: (name: string) => `כל האירועים ב${name}`, events: (n: number) => `${n.toLocaleString("he")} אירועים`,
  },
  en: {
    title: "Cities", intro: "Every city with upcoming events, by country: Israel's cities and cities abroad.",
    all: (name: string) => `All events in ${name}`, events: (n: number) => `${n.toLocaleString("en")} events`,
  },
};

export default function CitiesPage() {
  const { lang } = useT();
  const p = TEXT[lang];
  useTitle(p.title);
  const { data } = useQuery({
    queryKey: ["cities-index", lang],
    queryFn: async () => {
      const res = await fetch(`/cities-index?lang=${lang}`);
      if (!res.ok) throw new Error(`${res.status} /cities-index`);
      return (await res.json()) as Country[];
    },
    staleTime: 5 * 60_000,
  });
  return (
    <main>
      <Hero title={p.title}><p className="sub">{p.intro}</p></Hero>
      <div className="wrap">
        {!data ? <Loader /> : data.map((c) => (
          <section key={c.code} className="related" aria-labelledby={`country-${c.code}`}>
            <h2 id={`country-${c.code}`} dir="auto">{c.name} <small style={{ color: "var(--muted)", fontSize: 14, fontWeight: 500 }}>· {p.events(c.events)}</small></h2>
            <div className="chips">
              {c.path !== "/" && <Link className="chip" to={c.path}>{p.all(c.name)}</Link>}
              {c.cities.map((city) => (
                <Link key={city.path} className="chip" to={city.path}>
                  <span dir="auto">{city.name}</span> <small>{city.events}</small>
                </Link>
              ))}
            </div>
          </section>
        ))}
      </div>
    </main>
  );
}
