// /places: the place categories (happy hours, chef restaurants, kosher, escape rooms...) with how many
// places each has and how many are open now (GET /places/categories). Each opens its genre page, where
// a place is one card (partylinks.placecards). Crawlers get the same list from placecards/pages.py.
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { GENRE_ICON } from "../genreIcons";
import { genreLabel } from "../i18n";
import { Hero, Loader, useT, useTitle } from "../ui";

interface PlaceCategories {
  categories: { genre: string; places: number; open_now: number }[];
  open_now: number;
}

const TEXT = {
  he: { title: "מקומות", intro: "האפי האוורים, מסעדות שף, מקומות כשרים, חדרי בריחה ועוד: כל מקום פעם אחת, עם השעות והדיל שלו.", places: "מקומות", openNow: "פתוחים עכשיו", now: "מה פתוח עכשיו" },
  en: { title: "Places", intro: "Happy hours, chef restaurants, kosher places, escape rooms and more: each place once, with its hours and deal.", places: "places", openNow: "open now", now: "What's open now" },
};

export default function PlacesPage() {
  const { lang } = useT();
  const p = TEXT[lang];
  useTitle(p.title);
  const { data } = useQuery({
    queryKey: ["places", "categories"],
    queryFn: async () => {
      const res = await fetch("/places/categories");
      if (!res.ok) throw new Error(`${res.status} /places/categories`);
      return (await res.json()) as PlaceCategories;
    },
    staleTime: 60_000,
  });
  return (
    <main>
      <Hero title={p.title}><p>{p.intro}</p></Hero>
      <div className="wrap">
        {!data ? <Loader /> : (
          <div className="map-cats places-cats">
            <Link className="map-cat on" to="/now">
              <span aria-hidden>🟢</span><b>{p.now}</b><small>{data.open_now}</small>
            </Link>
            {data.categories.map((c) => (
              <Link key={c.genre} className="map-cat" to={`/genre/${c.genre}`}>
                <span aria-hidden>{GENRE_ICON[c.genre] ?? "📍"}</span>
                <b>{genreLabel(lang, c.genre)}</b>
                <small>{c.places} {p.places}{c.open_now ? ` · ${c.open_now} ${p.openNow}` : ""}</small>
              </Link>
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
