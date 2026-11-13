// The artists index's filter: category (the genre filter's families), genre, where (Israel's regions /
// countries abroad) and order, from each artist's own upcoming events (counted by GET /facets/artists, partylinks.facet_list).
// The choice lives in the URL (/artists?cat=g_parties&genre=techno&where=abroad&sort=popular), so it can be shared.
import { useSearchParams } from "react-router-dom";
import { FilterRow, GROUP_ICON } from "../Filters";
import { genreLabel } from "../i18n";
import { Chip, countryName, label, useT } from "../ui";

const abroad = (w: string) => /^[A-Z]{2}$/.test(w); // ISO country codes; Israel's region keys are lower case

/** The filter's chips with their counts, worked out by the server (GET /facets/artists → chips). */
export interface ArtistChips {
  cats: [string, number][];
  genres: [string, number][];
  here: number;
  away: number;
  places: [string, number][];
  groups: { value: string; events: number }[];
}

/** The filter's choice (the query of GET /facets/artists) and its rows, drawn from the server's counts. */
export function useArtistFilter(chips: ArtistChips | undefined, total: number | undefined) {
  const { t, lang } = useT();
  const f = t.artistFilter;
  const [params, setParams] = useSearchParams();
  const cat = params.get("cat"), genre = params.get("genre"), where = params.get("where"), place = params.get("place");
  const az = params.get("sort") !== "popular"; // A-Z unless asked: a one-click "most events"
  const set = (next: Record<string, string | null>) =>
    setParams((p) => {
      for (const [k, v] of Object.entries(next)) if (v) p.set(k, v); else p.delete(k);
      return p;
    }, { replace: true });
  const cats = new Map(chips?.cats ?? []);
  const groups = chips?.groups ?? [];
  const genres = chips?.genres ?? [];
  const here = chips?.here ?? 0, away = chips?.away ?? 0;
  const places = chips?.places ?? [];
  const active = !!(cat || genre || where || place);
  const query = { cat: cat ?? undefined, genre: genre ?? undefined, where: where ?? undefined, place: place ?? undefined, sort: az ? undefined : "popular" };

  const rows = chips && (
    <div className="filters">
      <FilterRow label={f.category} scroll>
        <Chip on={!cat} onClick={() => set({ cat: null, genre: null })}>{f.all}</Chip>
        {groups.filter((g) => cats.has(g.value)).map((g) => (
          <Chip key={g.value} on={cat === g.value} onClick={() => set({ cat: cat === g.value ? null : g.value, genre: null })}>
            <span aria-hidden>{GROUP_ICON[g.value]}</span> {genreLabel(lang, g.value)} <small>{cats.get(g.value)}</small>
          </Chip>
        ))}
      </FilterRow>
      {genres.length > 1 && (
        <FilterRow label={f.genre} scroll>
          <Chip on={!genre} onClick={() => set({ genre: null })}>{f.all}</Chip>
          {genres.map(([g, n]) => (
            <Chip key={g} on={genre === g} onClick={() => set({ genre: genre === g ? null : g })}>{genreLabel(lang, g)} <small>{n}</small></Chip>
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
        <Chip on={az} onClick={() => set({ sort: null })}>{f.az}</Chip>
        <Chip on={!az} onClick={() => set({ sort: "popular" })}>{f.popular}</Chip>
        {total !== undefined && <small className="frow-count">{f.count(total)}</small>}
        {active && <button className="link-btn" onClick={() => set({ cat: null, genre: null, where: null, place: null })}>{f.clear}</button>}
      </FilterRow>
    </div>
  );
  return { query, rows, key: params.toString() };
}

/** The filter's rows, from the counts of the list's first page. */
export function ArtistFilterRows({ chips, total }: { chips: ArtistChips | undefined; total: number | undefined }) {
  return useArtistFilter(chips, total).rows || null;
}
