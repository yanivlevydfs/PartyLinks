// The artists index's filter: category (the genre filter's families), genre, where (Israel's regions /
// countries abroad) and order, from each artist's own upcoming events (GET /artists/tags, partylinks.artist_filter).
// The choice lives in the URL (/artists?cat=g_parties&genre=techno&where=abroad), so it can be shared.
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { getJSON, useCategories } from "../api";
import { FilterRow, GROUP_ICON } from "../Filters";
import type { Facet } from "../types";
import { Chip, countryName, label, useT } from "../ui";

type Tags = Record<string, { g: string[]; w: string[] }>;
const abroad = (w: string) => /^[A-Z]{2}$/.test(w); // ISO country codes; Israel's region keys are lower case

/** Counted chips of the values the artists carry, most common first. */
function counted(lists: string[][], keep: (v: string) => boolean = () => true): [string, number][] {
  const n = new Map<string, number>();
  for (const l of lists) for (const v of new Set(l)) if (keep(v)) n.set(v, (n.get(v) ?? 0) + 1);
  return [...n].sort((a, b) => b[1] - a[1]);
}

/** The artists the filter keeps (in its order: most events, or null for A-Z), and the filter's rows. */
export function useArtistFilter(facets: Facet[] | undefined) {
  const { t, lang } = useT();
  const f = t.artistFilter;
  const [params, setParams] = useSearchParams();
  const tags = useQuery({ queryKey: ["artist-tags"], queryFn: () => getJSON<Tags>("/artists/tags"), staleTime: 10 * 60_000 }).data;
  const groups = useCategories({ anywhere: "true" }).data?.genre_groups ?? [];
  const cat = params.get("cat"), genre = params.get("genre"), where = params.get("where"), place = params.get("place");
  const az = params.get("sort") === "az";
  const set = (next: Record<string, string | null>) =>
    setParams((p) => {
      for (const [k, v] of Object.entries(next)) if (v) p.set(k, v); else p.delete(k);
      return p;
    }, { replace: true });

  const of = (a: Facet) => tags?.[a.value.toLocaleLowerCase()] ?? tags?.[a.value.toLowerCase()];
  const members = groups.find((g) => g.value === cat)?.members ?? [];
  const inWhere = (w: string[]) => !where || (where === "abroad" ? w.some(abroad) : w.some((x) => !abroad(x)));
  // Each row counts the artists the other rows keep, so a chip never leads to an empty list.
  const keep = (a: Facet, skip: "cat" | "genre" | "where" | "place" | null) => {
    const tg = of(a);
    if (!tg) return !cat && !genre && !where && !place;
    return (skip === "cat" || !cat || tg.g.includes(cat)) && (skip === "genre" || !genre || tg.g.includes(genre))
      && (skip === "where" || inWhere(tg.w)) && (skip === "place" || !place || tg.w.includes(place));
  };
  const pool = (skip: "cat" | "genre" | "where" | "place") => (facets ?? []).filter((a) => keep(a, skip)).map((a) => of(a));
  const cats = new Map(counted(pool("cat").map((x) => x?.g ?? [])));
  const genres = cat ? counted(pool("genre").map((x) => x?.g ?? []), (g) => members.includes(g)) : [];
  const wheres = counted(pool("where").map((x) => x?.w ?? []));
  const here = wheres.filter(([w]) => !abroad(w)).length, away = wheres.filter(([w]) => abroad(w)).length;
  const places = where ? counted(pool("place").map((x) => x?.w ?? []), (w) => (where === "abroad") === abroad(w)) : [];

  const shown = facets && tags ? facets.filter((a) => keep(a, null)) : facets;
  const ordered = az || !shown ? shown : [...shown].sort((a, b) => b.events - a.events);
  const active = !!(cat || genre || where || place);

  const rows = tags && (
    <div className="filters">
      <FilterRow label={f.category} scroll>
        <Chip on={!cat} onClick={() => set({ cat: null, genre: null })}>{f.all}</Chip>
        {groups.filter((g) => cats.has(g.value)).map((g) => (
          <Chip key={g.value} on={cat === g.value} onClick={() => set({ cat: cat === g.value ? null : g.value, genre: null })}>
            <span aria-hidden>{GROUP_ICON[g.value]}</span> {label(t.genre, g.value)} <small>{cats.get(g.value)}</small>
          </Chip>
        ))}
      </FilterRow>
      {genres.length > 1 && (
        <FilterRow label={f.genre} scroll>
          <Chip on={!genre} onClick={() => set({ genre: null })}>{f.all}</Chip>
          {genres.map(([g, n]) => (
            <Chip key={g} on={genre === g} onClick={() => set({ genre: genre === g ? null : g })}>{label(t.genre, g)} <small>{n}</small></Chip>
          ))}
        </FilterRow>
      )}
      <FilterRow label={f.where} scroll>
        <Chip on={!where} onClick={() => set({ where: null, place: null })}>{f.all}</Chip>
        {here > 0 && <Chip on={where === "israel"} onClick={() => set({ where: where === "israel" ? null : "israel", place: null })}>📍 {f.israel}</Chip>}
        {away > 0 && <Chip on={where === "abroad"} onClick={() => set({ where: where === "abroad" ? null : "abroad", place: null })}>✈️ {f.abroad}</Chip>}
        {places.map(([w, n]) => (
          <Chip key={w} on={place === w} onClick={() => set({ place: place === w ? null : w })}>
            {abroad(w) ? countryName(w, lang) || w : label(t.areas, w)} <small>{n}</small>
          </Chip>
        ))}
      </FilterRow>
      <FilterRow label={f.sort}>
        <Chip on={!az} onClick={() => set({ sort: null })}>{f.popular}</Chip>
        <Chip on={az} onClick={() => set({ sort: "az" })}>{f.az}</Chip>
        {shown && <small className="frow-count">{f.count(shown.length)}</small>}
        {active && <button className="link-btn" onClick={() => set({ cat: null, genre: null, where: null, place: null })}>{f.clear}</button>}
      </FilterRow>
    </div>
  );
  return { facets: ordered, sorted: !az, rows, key: params.toString() };
}
