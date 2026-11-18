// /cities: find a city fast. A search box over every city (any country), a row of countries (Israel
// first), the chosen country's busiest cities as tiles, then all its cities A to Z (Israel's by region).
// GET /cities-index, the same list the server renders for crawlers (seo.cities_page). ?country=FR keeps the choice.
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Hero, Loader, useT, useTitle } from "../ui";
import "../city/city.css";

interface CityLink { path: string; name: string; events: number; region?: string }
interface Country { code: string; name: string; path: string; events: number; cities: CityLink[]; regions?: { key: string; name: string }[] }

const TOP = 12;

const TEXT = {
  he: {
    title: "ערים", intro: "בחרו מדינה או חפשו עיר: כל עיר עם האירועים הקרובים שלה.", search: "חיפוש עיר…",
    top: "הכי הרבה אירועים", all: "כל הערים א־ת", allIn: (name: string) => `כל האירועים ב${name}`, none: (q: string) => `לא נמצאה עיר בשם "${q}".`,
    events: (n: number) => `${n.toLocaleString("he")} אירועים`, countries: "מדינות",
  },
  en: {
    title: "Cities", intro: "Pick a country or search for a city: every city with its upcoming events.", search: "Search a city…",
    top: "Most events", all: "All cities A–Z", allIn: (name: string) => `All events in ${name}`, none: (q: string) => `No city called "${q}".`,
    events: (n: number) => `${n.toLocaleString("en")} events`, countries: "Countries",
  },
};

export default function CitiesPage() {
  const { lang } = useT();
  const p = TEXT[lang];
  useTitle(p.title);
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState("");
  const { data } = useQuery({
    queryKey: ["cities-index", lang],
    queryFn: async () => {
      const res = await fetch(`/cities-index?lang=${lang}`);
      if (!res.ok) throw new Error(`${res.status} /cities-index`);
      return (await res.json()) as Country[];
    },
    staleTime: 5 * 60_000,
  });
  const country = data?.find((c) => c.code === params.get("country")) ?? data?.[0];
  const pick = (code: string) => {
    setQ("");
    setParams(code === data?.[0]?.code ? {} : { country: code }, { replace: true });
  };
  // Search: every city of every country, names starting with the text first, then the busiest.
  const needle = q.trim().toLocaleLowerCase();
  const found = useMemo(() => !needle || !data ? [] : data
    .flatMap((c) => c.cities.map((city) => ({ ...city, country: c })))
    .filter((c) => c.name.toLocaleLowerCase().includes(needle))
    .sort((a, b) => +b.name.toLocaleLowerCase().startsWith(needle) - +a.name.toLocaleLowerCase().startsWith(needle) || b.events - a.events)
    .slice(0, 30), [data, needle]);

  return (
    <main>
      <Hero title={p.title}>
        <p className="sub">{p.intro}</p>
        <input className="search" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={p.search} aria-label={p.search} />
      </Hero>
      <div className="wrap cities">
        {!data || !country ? <Loader /> : needle ? (
          found.length ? (
            <div className="cities-tiles">
              {found.map((c) => (
                <Link key={c.path} className="cities-tile" to={c.path}>
                  <b dir="auto">{c.name}</b>
                  <small>{c.country.name} · {p.events(c.events)}</small>
                </Link>
              ))}
            </div>
          ) : <p className="msg">{p.none(q.trim())}</p>
        ) : (
          <>
            <nav className="cities-countries" aria-label={p.countries}>
              {data.map((c) => (
                <button key={c.code} type="button" className={"chip" + (c.code === country.code ? " on" : "")}
                  aria-pressed={c.code === country.code} onClick={() => pick(c.code)}>
                  {c.name} <small>{c.cities.length}</small>
                </button>
              ))}
            </nav>
            <h2 className="cities-h">
              {country.name} <small>· {p.events(country.events)}</small>
              {country.path !== "/" && <Link className="cities-all-link" to={country.path}>{p.allIn(country.name)} {lang === "he" ? "←" : "→"}</Link>}
            </h2>
            <h3 className="cities-sub">{p.top}</h3>
            <div className="cities-tiles">
              {country.cities.slice(0, TOP).map((c) => (
                <Link key={c.path} className="cities-tile" to={c.path}>
                  <b dir="auto">{c.name}</b><small>{p.events(c.events)}</small>
                </Link>
              ))}
            </div>
            {country.cities.length > TOP && <AllCities country={country} title={p.all} />}
          </>
        )}
      </div>
    </main>
  );
}

/** Every city of the country A to Z; Israel's under their regions. */
function AllCities({ country, title }: { country: Country; title: string }) {
  const { lang } = useT();
  const az = (list: CityLink[]) => [...list].sort((a, b) => a.name.localeCompare(b.name, lang));
  const groups = country.regions
    ? country.regions.map((r) => ({ key: r.key, name: r.name, cities: az(country.cities.filter((c) => (c.region ?? "other") === r.key)) }))
      .filter((g) => g.cities.length)
    : [{ key: "all", name: "", cities: az(country.cities) }];
  return (
    <section>
      <h3 className="cities-sub">{title}</h3>
      {groups.map((g) => (
        <div key={g.key} className="cities-group">
          {g.name && <h4>{g.name} <small>{g.cities.length}</small></h4>}
          <ul className="cities-az">
            {g.cities.map((c) => <li key={c.path}><Link to={c.path} dir="auto">{c.name}</Link> <small>{c.events}</small></li>)}
          </ul>
        </div>
      ))}
    </section>
  );
}
