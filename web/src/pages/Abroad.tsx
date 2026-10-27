// Concerts and festivals abroad: their own feed, kept out of the Israeli one (API ?abroad=true).
// /abroad/<country>: one country (its code and title come from the server, like the page crawlers get).
import { useEffect, useState } from "react";
import { useLocation, useParams, useSearchParams } from "react-router-dom";
import { useCategories, useLandingInfo } from "../api";
import { EventGrid, Hero, Related, Select, useT, useTitle } from "../ui";

export default function Abroad() {
  const { t, lang } = useT();
  const { country } = useParams();
  const { pathname } = useLocation();
  const info = useLandingInfo(pathname, lang).data;
  const title = (country && info?.title) || t.abroad.title;
  useTitle(title);
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  // Filters: every option comes from the stored events (the categories facets), never a fixed list.
  // Kept in the URL (?country=&city=&from=&to=): a filtered view is a shareable link and survives a reload.
  const [params, setParams] = useSearchParams();
  const [pick, city, from, to] = ["country", "city", "from", "to"].map((k) => params.get(k) ?? "");
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) if (v) next.set(k, v); else next.delete(k);
    setParams(next, { replace: true });
  };
  const code = country ? info?.country ?? undefined : pick || undefined;
  const countries = useCategories({ abroad: "true" }).data?.countries ?? [];
  const cities = useCategories({ abroad: "true", country: code }).data?.cities ?? [];
  const names = new Intl.DisplayNames([lang], { type: "region" });
  const name = (c: string) => { try { return names.of(c) ?? c; } catch { return c; } };
  useEffect(() => {
    const id = setTimeout(() => setQ(text.trim()), 300);
    return () => clearTimeout(id);
  }, [text]);
  return (
    <>
      <Hero title={title}>
        <p className="sub">{(country && info?.intro) || t.abroad.intro}</p>
        <input className="search" type="search" value={text} onChange={(e) => setText(e.target.value)} placeholder={t.search} aria-label={t.search} />
      </Hero>
      <div className="wrap abroad-filters">
        {!country && (
          <div className="sort">
            <span className="sort-label">{t.abroad.country}</span>
            <Select value={pick} onChange={(v) => set({ country: v, city: "" })} label={t.abroad.country}
              options={[["", t.abroad.allCountries], ...countries.map((c): [string, string] => [c.value, `${name(c.value)} (${c.events})`])]} />
          </div>
        )}
        <div className="sort">
          <span className="sort-label">{t.abroad.city}</span>
          <Select value={city} onChange={(v) => set({ city: v })} label={t.abroad.city}
            options={[["", t.abroad.allCities], ...cities.map((c): [string, string] => [c.value, `${c.value} (${c.events})`])]} />
        </div>
        <label className="sort">
          <span className="sort-label">{t.abroad.from}</span>
          <input type="date" value={from} max={to || undefined} onChange={(e) => set({ from: e.target.value })} aria-label={t.abroad.from} />
        </label>
        <label className="sort">
          <span className="sort-label">{t.abroad.to}</span>
          <input type="date" value={to} min={from || undefined} onChange={(e) => set({ to: e.target.value })} aria-label={t.abroad.to} />
        </label>
        {(pick || city || from || to) && (
          <button className="f-clear" onClick={() => set({ country: "", city: "", from: "", to: "" })}>{t.abroad.clear}</button>
        )}
      </div>
      <main className="wrap">
        {(!country || info?.country) && (
          <EventGrid params={{
            abroad: "true", country: code, city: city || undefined, q: q.length >= 2 ? q : undefined,
            date_from: from ? `${from}T00:00:00` : undefined, date_to: to ? `${to}T23:59:59` : undefined,
          }} />
        )}
        <Related path={pathname} />
      </main>
    </>
  );
}
