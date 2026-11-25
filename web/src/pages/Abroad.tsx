// Concerts and festivals abroad: their own feed, kept out of the Israeli one (API ?abroad=true).
// /abroad/<country>: one country (its code and title come from the server, like the page crawlers get).
// /abroad/<country>/<city>: one city there (its names come from the server too); .../tonight, .../weekend, .../<genre>: its sub-pages.
import { useEffect, useMemo, useState } from "react";
import { useLocation, useParams, useSearchParams } from "react-router-dom";
import { useCategories, useLandingInfo, useSuggest } from "../api";
import { CityBar } from "../city/CityBar";
import { ScopedEvents } from "../filter_scope/ScopedEvents";
import { locate } from "../filter_scope/NearMe";
import { whenParams } from "../Filters";
import type { Facet } from "../types";
import { Chip, Hero, Related, Select, useT, useTitle } from "../ui";
import { useWeb } from "../site_params/useWeb";

const UNKNOWN = "ZZ"; // the server's code for an event abroad in an unknown country

export default function Abroad() {
  const { t, lang } = useT();
  const { country, city: place, sub } = useParams();
  const { pathname } = useLocation();
  const info = useLandingInfo(pathname, lang).data;
  const title = (country && info?.title) || t.abroad.title;
  useTitle(title);
  // Filters: every option comes from the stored events (the categories facets), never a fixed list.
  // Kept in the URL (?q=&country=&city=&from=&to=&source=): a filtered view is a shareable link and survives a reload.
  const [params, setParams] = useSearchParams();
  const [q, pick, city, from, to, source] = ["q", "country", "city", "from", "to", "source"].map((k) => params.get(k) ?? "");
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) if (v) next.set(k, v); else next.delete(k);
    setParams(next, { replace: true });
  };
  const [text, setText] = useState(q);
  const [suggest, setSuggest] = useState(false);
  const debounce = useWeb().search_debounce_ms;
  useEffect(() => {
    const id = setTimeout(() => text.trim() !== q && set({ q: text.trim() }), debounce);
    return () => clearTimeout(id);
  }, [text]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => setText(q), [q]);

  const code = country ? info?.country ?? undefined : pick || undefined;
  // A city's page: the server's name for it (the API finds its other spellings: "Paris" is "פריז" too).
  const where = place ? info?.city?.en : city || undefined;
  const subParams = sub === "tonight" || sub === "weekend" ? whenParams(sub) : sub ? { genre: sub } : {};
  // Without the venue and artist lists (lists=false): the page shows neither, and they were most of Germany's 782 KB.
  const all = useCategories({ abroad: "true", lists: "false" }).data;
  // A country's cities only: the list follows the chosen country.
  const cities = useCategories({ abroad: "true", country: code, lists: "false" }).data?.cities ?? [];
  // Artists and venues as typed, from the server's index (a few names), not the whole list in the page.
  const named = useSuggest(text.trim()).data;
  // "My location": the nearest city abroad with coming events (GET /locate), its country and city picked.
  const [near, setNear] = useState<"idle" | "busy" | "fail">("idle");
  const nearMe = async () => {
    setNear("busy");
    const found = await locate();
    if (!found?.country || !found.abroad_city || (country && found.country !== code)) return setNear("fail");
    setNear("idle");
    go(country ? { city: found.abroad_city, q: "" } : { country: found.country, city: found.abroad_city, q: "" });
  };
  const names = useMemo(() => new Intl.DisplayNames([lang], { type: "region" }), [lang]);
  const name = (c: string) => {
    if (c === UNKNOWN) return t.abroad.unknownCountry;
    try { return names.of(c) ?? c; } catch { return c; }
  };
  // A to Z in the page's language, the unknown country last.
  const countries = [...(all?.countries ?? [])].sort((a, b) =>
    +(a.value === UNKNOWN) - +(b.value === UNKNOWN) || name(a.value).localeCompare(name(b.value), lang));
  const byName = (list: Facet[]) => [...list].sort((a, b) => a.value.localeCompare(b.value, lang));
  const go = (patch: Record<string, string>) => { set(patch); setSuggest(false); };

  return (
    <>
      <Hero title={title}>
        <p className="sub">{(country && info?.intro) || t.abroad.intro}</p>
        {/* Suggestions stay open while focus is anywhere in the box (Tab into them), close on leaving or Escape. */}
        <div className="search-box" onFocus={() => setSuggest(true)} onKeyDown={(e) => e.key === "Escape" && setSuggest(false)}
          onBlur={(e) => !e.currentTarget.contains(e.relatedTarget as Node | null) && setSuggest(false)}>
          <input className="search" type="search" value={text} onChange={(e) => setText(e.target.value)}
            placeholder={t.abroad.searchHint} aria-label={t.search} />
          {suggest && all && (
            <Suggestions text={text} countries={country ? [] : countries} cities={place ? [] : all.cities} artists={named?.artists ?? []} venues={named?.venues ?? []}
              countryName={name} onCountry={(c) => go({ country: c, city: "", q: "" })} onCity={(c) => go({ city: c, q: "" })}
              onText={(v) => { setText(v); go({ q: v }); }} />
          )}
        </div>
      </Hero>
      <div className="wrap abroad-filters">
        {!country && (
          <div className="sort">
            <span className="sort-label">{t.abroad.country}</span>
            <Select value={pick} onChange={(v) => set({ country: v, city: "" })} label={t.abroad.country}
              options={[["", t.abroad.allCountries], ...countries.map((c): [string, string] => [c.value, `${name(c.value)} (${c.events})`])]} />
          </div>
        )}
        {!place && (
          <div className="sort">
            <span className="sort-label">{t.abroad.city}</span>
            <Select value={city} onChange={(v) => set({ city: v })} label={t.abroad.city}
              options={[["", t.abroad.allCities], ...byName(cities).map((c): [string, string] => [c.value, `${c.value} (${c.events})`])]} />
          </div>
        )}
        {!place && (
          <Chip on={false} onClick={nearMe} className="near">
            📍 {near === "busy" ? t.quiz.locating : near === "fail" ? t.abroad.noNear : t.abroad.near}
          </Chip>
        )}
        {(pick || city || from || to || q || source) && (
          <button className="f-clear" onClick={() => { setText(""); set({ q: "", country: "", city: "", from: "", to: "", source: "" }); }}>{t.abroad.clear}</button>
        )}
      </div>
      <main className="wrap">
        {place && <CityBar />}
        {(!country || info?.country) && (!place || where) && (
          // The site's full filter (dates, genres, price, shops, time of day, sort), counted within this country / city.
          // Old links' ?from= / ?to= / ?source= still narrow the base. Keyed by place: a new place starts a clean filter.
          <ScopedEvents
            key={`${code ?? ""}|${where ?? ""}|${sub ?? ""}`}
            base={{
              abroad: "true", country: code, city: where, q: q.length >= 2 ? q : undefined,
              date_from: from ? `${from}T00:00:00` : undefined, date_to: to ? `${to}T23:59:59` : undefined,
              source: source || undefined, ...subParams,
            }}
            empty={(page) => page?.did_you_mean ? (
              <div className="msg">
                <p>{t.closest(q)}</p>
                <button className="f-pill" onClick={() => { setText(page.did_you_mean!); set({ q: page.did_you_mean! }); }}>{page.did_you_mean}</button>
              </div>
            ) : <p className="msg">{q ? t.noMatch(q) : t.empty}</p>}
          />
        )}
        <Related path={pathname} />
      </main>
    </>
  );
}

/** Countries, cities, artists and venues abroad matching the typed text, from the first letter. A country
 *  or a city sets that filter; an artist or a venue searches for it. */
function Suggestions({ text, countries, cities, artists, venues, countryName, onCountry, onCity, onText }: {
  text: string; countries: Facet[]; cities: Facet[]; artists: Facet[]; venues: Facet[];
  countryName: (code: string) => string; onCountry: (code: string) => void; onCity: (city: string) => void; onText: (q: string) => void;
}) {
  const { t } = useT();
  const needle = text.trim().toLocaleLowerCase();
  if (!needle) return null;
  // Names starting with the text first, then the busiest.
  const pick = (list: Facet[], label: (f: Facet) => string, n: number) => list
    .filter((f) => label(f).toLocaleLowerCase().includes(needle))
    .sort((a, b) => +label(b).toLocaleLowerCase().startsWith(needle) - +label(a).toLocaleLowerCase().startsWith(needle) || b.events - a.events)
    .slice(0, n);
  const items = [
    ...pick(countries, (c) => countryName(c.value), 3).map((c) => ({ key: "k" + c.value, icon: "🌍", label: countryName(c.value), n: c.events, act: () => onCountry(c.value) })),
    ...pick(cities, (c) => c.value, 3).map((c) => ({ key: "c" + c.value, icon: "📍", label: c.value, n: c.events, act: () => onCity(c.value) })),
    ...pick(artists, (a) => a.value, 4).map((a) => ({ key: "a" + a.value, icon: "🎧", label: a.value, n: a.events, act: () => onText(a.value) })),
    ...pick(venues, (v) => v.value, 3).map((v) => ({ key: "v" + v.value, icon: "🏛", label: v.value, n: v.events, act: () => onText(v.value) })),
  ];
  if (!items.length) return null;
  return (
    <ul className="suggest" aria-label={t.search}>
      {items.map((i) => (
        <li key={i.key}><button type="button" onClick={i.act}>
          <span aria-hidden>{i.icon}</span><b dir="auto">{i.label}</b><small>{t.results(i.n)}</small>
        </button></li>
      ))}
    </ul>
  );
}
