import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import { useWeb } from "./site_params/useWeb";
import type { Categories, EventDetail, EventPage, EventSummary, MapEvent, MapPins, Post, PostList, SponsoredRails } from "./types";

export type Params = Record<string, string | string[] | undefined>;

export async function getJSON<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error(`${res.status} ${url}`);
  return res.json();
}

/** getJSON reading the body as it arrives, telling `onProgress` the share received (0-1). The server's
 *  X-Body-Length is the JSON's size before gzip: Content-Length is the zipped size, while the stream
 *  counts unzipped bytes. Without that header: plain getJSON, no progress. */
export async function getJSONProgress<T>(url: string, onProgress: (share: number) => void, signal?: AbortSignal): Promise<T> {
  onProgress(0);
  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error(`${res.status} ${url}`);
  const total = Number(res.headers.get("x-body-length"));
  if (!total || !res.body) return res.json();
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    got += value.length;
    onProgress(Math.min(1, got / total));
  }
  const body = new Uint8Array(got);
  let at = 0;
  for (const chunk of chunks) {
    body.set(chunk, at);
    at += chunk.length;
  }
  return JSON.parse(new TextDecoder().decode(body)) as T;
}

/** The loader's real percent (0-100) and the onProgress that sets it: `<Loader progress={loaded || undefined} />`. */
export function useLoadShare(): [number, (share: number) => void] {
  const [loaded, setLoaded] = useState(0);
  return [loaded, useCallback((share: number) => setLoaded(Math.floor(share * 100)), [])];
}

/** getJSONProgress when there is an onProgress, else getJSON. */
const getWith = <T,>(url: string, onProgress?: (share: number) => void, signal?: AbortSignal) =>
  onProgress ? getJSONProgress<T>(url, onProgress, signal) : getJSON<T>(url, signal);

export function toQuery(params: Params): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    for (const item of Array.isArray(v) ? v : [v]) if (item) qs.append(k, item);
  }
  return qs.toString();
}

// The first page of the last few lists abroad, kept in localStorage: a return visit (or a reload) draws the cards
// at once while the fresh page loads behind them, instead of a loader for the 1-3 s a cold city takes on the server.
const KEPT_PAGES = "q:events-kept";
const KEPT_MAX = 12;
type KeptPage = { qs: string; at: number; page: EventPage };
function keptPages(): KeptPage[] {
  try { return (JSON.parse(localStorage.getItem(KEPT_PAGES) ?? "[]") as KeptPage[]) ?? []; } catch { return []; }
}
function keepPage(qs: string, page: EventPage) {
  try {
    const rest = keptPages().filter((k) => k.qs !== qs).slice(0, KEPT_MAX - 1);
    localStorage.setItem(KEPT_PAGES, JSON.stringify([{ qs, at: Date.now(), page }, ...rest]));
  } catch { /* full or blocked: only slower */ }
}

/** `onProgress`: the first page's share received (0-1), for the loader's real percent. */
export function useEvents(params: Params, onProgress?: (share: number) => void) {
  const qs = toQuery({ page_size: String(useWeb().events_page_size), ...params });
  const keep = params.abroad === "true";
  const kept = keep ? keptPages().find((k) => k.qs === qs) : undefined;
  return useInfiniteQuery({
    queryKey: ["events", qs],
    // signal: a request overtaken by the next keystroke or filter is cancelled, not left to load the server.
    queryFn: async ({ pageParam, signal }) => {
      const page = await getWith<EventPage>(`/events?${qs}&page=${pageParam}`, pageParam === 1 ? onProgress : undefined, signal);
      if (keep && pageParam === 1) keepPage(qs, page);
      return page;
    },
    initialData: kept ? { pages: [kept.page], pageParams: [1] } : undefined,
    initialDataUpdatedAt: kept?.at,
    placeholderData: (prev) => prev, // the old results stay (dimmed) while the new ones load: no flash of skeletons
    initialPageParam: 1,
    getNextPageParam: (last) => (last.page < 40 && last.page * last.page_size < last.total ? last.page + 1 : undefined), // 40: the API's max_page
  });
}

export interface Suggested { value: string; events: number; verified?: boolean; image_url?: string; area?: string }

/** Artists and venues matching what is typed in the search box (GET /search/suggest), asked after a short
 *  pause in typing; a request overtaken by the next keystroke is cancelled (signal). */
export function useSuggest(q: string) {
  const [text, setText] = useState(q);
  useEffect(() => {
    const id = setTimeout(() => setText(q), 120);
    return () => clearTimeout(id);
  }, [q]);
  return useQuery({
    queryKey: ["suggest", text],
    queryFn: ({ signal }) => getJSON<{ artists: Suggested[]; venues: Suggested[] }>(`/search/suggest?q=${encodeURIComponent(text)}`, signal),
    enabled: text.length >= 2,
    staleTime: 5 * 60_000,
    placeholderData: (prev) => prev, // the last names stay while the next ones load
  });
}

export interface CityLink { path: string; name: string; events: number; region?: string }
export interface CountryCities { code: string; name: string; path: string; events: number; cities: CityLink[]; regions?: { key: string; name: string }[] }

/** Every city with a page, by country, Israel first (GET /cities-index): the /cities page and the search box's suggestions. */
export function useCitiesIndex(lang: string, enabled = true, onProgress?: (share: number) => void) {
  return useQuery({
    queryKey: ["cities-index", lang],
    queryFn: ({ signal }) => getWith<CountryCities[]>(`/cities-index?lang=${lang}`, onProgress, signal),
    staleTime: 5 * 60_000,
    enabled,
  });
}

/** Every event matching the filter that has coordinates, in one request, for the map. `onProgress`:
 *  the share of the answer received so far (0-1), for the loader's real percent. */
export function useMapEvents(params: Params = {}, onProgress: (share: number) => void = () => {}) {
  const qs = toQuery({ ...params, compact: "true" });
  return useQuery({
    queryKey: ["events", "map", qs],
    queryFn: async ({ signal }) => mapPins(await getJSONProgress<MapColumns>(`/events/map?${qs}`, onProgress, signal)),
  });
}

/** GET /events/map?compact=true (partylinks.api map_columns): one row of values per pin, repeated names as
 *  indexes into the lookup tables, times in Unix seconds. 68k pins were 32 MB as objects (25.11.2026). */
type MapColumns = {
  rows: [string, string | null, string | null, string, number | null, number | null, 0 | 1, number | null, string | null, number | null, number, number, number][];
  venues: string[];
  zones: string[];
  site_sets: string[][];
  unplaced: number;
};

const isoAt = (seconds: number | null) => (seconds == null ? null : new Date(seconds * 1000).toISOString());

/** The compact answer as the pins the map works with (MapEvent, the same fields as before). */
export function mapPins(c: MapColumns): MapPins {
  return {
    unplaced: c.unplaced,
    items: c.rows.map(([id, code, slug, title, start, end, known, zone, day, venue, lat, lng, sites]) => {
      const sources = c.site_sets[sites] ?? [];
      return {
        id, code, slug, title, start_at: isoAt(start), end_at: isoAt(end), time_known: known === 1,
        time_zone: zone == null ? null : c.zones[zone], local_date: day,
        venue_name: venue == null ? null : c.venues[venue],
        latitude: lat, longitude: lng, source: sources[0] ?? "", sources,
      } satisfies MapEvent;
    }),
  };
}

/** An event page's request answered 410 (the event is over) or 404 (no such event). */
export const isGone = (error: Error | null) => /^(404|410) /.test(error?.message ?? "");

export const useEvent = (id: string, onProgress?: (share: number) => void) =>
  useQuery({
    queryKey: ["event", id],
    queryFn: ({ signal }) => getWith<EventDetail>(`/events/${encodeURIComponent(id)}`, onProgress, signal),
    retry: (count, error) => !isGone(error) && count < 1, // an ended event will not come back on a retry
  });

/** Facet counts, of every upcoming event or (params) of those matching a filter. */
export function useCategories(params: Params = {}) {
  const qs = toQuery(params);
  return useQuery({
    queryKey: ["categories", qs],
    queryFn: () => getJSON<Categories>("/categories" + (qs ? `?${qs}` : "")),
    staleTime: 5 * 60_000,
    placeholderData: (prev) => prev, // the next question keeps its options while its counts refresh
  });
}

export const usePosts = (lang: string, category?: string, onProgress?: (share: number) => void) =>
  useQuery({
    queryKey: ["posts", lang, category],
    queryFn: ({ signal }) => getWith<PostList>(`/posts?lang=${lang}${category ? `&category=${encodeURIComponent(category)}` : ""}`, onProgress, signal),
  });

/** A query kept in localStorage too: a return visit draws the last copy at once while a fresh one loads. */
function useKept<T, R = T>(key: string, url: string, select?: (data: T) => R) {
  const store = `q:${key}`;
  return useQuery({
    select,
    queryKey: [key],
    queryFn: async () => {
      const data = await getJSON<T>(url);
      try { localStorage.setItem(store, JSON.stringify({ at: Date.now(), data })); } catch { /* full or blocked: only slower */ }
      return data;
    },
    initialData: () => {
      try { return (JSON.parse(localStorage.getItem(store) ?? "null") as { data: T } | null)?.data; } catch { return undefined; }
    },
    initialDataUpdatedAt: () => {
      try { return (JSON.parse(localStorage.getItem(store) ?? "null") as { at: number } | null)?.at; } catch { return undefined; }
    },
    staleTime: 5 * 60_000,
    // The kept copy draws at once; every page load still fetches a fresh one, or an admin change
    // (sponsored picks) would wait up to staleTime behind a copy that Ctrl+F5 doesn't clear.
    refetchOnMount: "always",
  });
}

/** The home page's paid side rails (picked in the admin). */
export const useSponsored = () => useKept<SponsoredRails>("sponsored", "/events/sponsored");

/** An Israeli holiday's next dates (GET /holidays/list, partylinks.holiday_pages): eve to last day, local dates. */
export interface Holiday { key: string; he: string; en: string; start: string; end: string; events: number; page: boolean }
export const useHolidays = () =>
  useQuery({ queryKey: ["holidays"], queryFn: () => getJSON<{ holidays: Holiday[] }>("/holidays/list"), staleTime: 10 * 60_000 });
/** A holiday's dates as the filter's `when` range. */
export const holidayWhen = (h: Pick<Holiday, "start" | "end">) => `${h.start}~${h.end}`;

/** The home page's rows under "Trending": a pool of the busiest events per row (settings home_rails, admin config tab);
 *  `holiday`: the coming holiday its "holiday" row is about. */
export interface Rails { rails: Record<string, EventSummary[]>; holiday?: Holiday | null }
/** /events/rails?compact=true: each event once and a row as its ids (1,865 cards were 1,176 events, 3.9MB parsed and
 *  kept in localStorage on every visit). The kept copy is this small form; the rows are rebuilt from it. */
interface CompactRails { events: Record<string, EventSummary>; rails: Record<string, string[]>; pinned?: Record<string, string[]>; holiday?: Holiday | null }
const expandRails = (c: CompactRails): Rails => ({
  holiday: c.holiday,
  rails: Object.fromEntries(Object.entries(c.rails ?? {}).map(([key, ids]) => {
    const pins = new Set(c.pinned?.[key] ?? []);
    return [key, ids.flatMap((id) => (c.events[id] ? [pins.has(id) ? { ...c.events[id], pinned: true } : c.events[id]] : []))];
  })),
});
// "rails2": a new key, so the old full copy (q:rails, ~4MB) is not read as the compact one; it is removed below.
export const useRails = () => useKept<CompactRails, Rails>("rails2", "/events/rails?compact=true", expandRails);
try { localStorage.removeItem("q:rails"); } catch { /* blocked: nothing kept anyway */ }

export interface TicketSite { name: string; events: number; updated_at: string | null }
/** A box office inside a platform (a Smarticket tenant: a city's culture hall, a council, a theatre). */
export interface BoxOffice { source: string; name: string; city: string | null; url: string; events: number }

/** The stores page: upcoming events per ticket site, with its last scrape. */
export const useTicketSites = (onProgress?: (share: number) => void) =>
  useQuery({ queryKey: ["ticket-sites"], queryFn: ({ signal }) => getWith<{ stores: TicketSite[]; total?: number; box_offices?: BoxOffice[] }>("/ticket-sites", onProgress, signal), staleTime: 5 * 60_000 });

export const usePost = (slug: string, lang: string, onProgress?: (share: number) => void) =>
  useQuery({ queryKey: ["post", slug, lang], queryFn: ({ signal }) => getWith<Post>(`/posts/${encodeURIComponent(slug)}?lang=${lang}`, onProgress, signal) });

export interface LandingInfo { title: string | null; intro: string | null; links: { path: string; label: string }[]; country: string | null; city: { he: string; en: string } | null;
  /** City pages: the city's centre [lat, lng] and its time zone (the forecast strip) */
  center?: [number, number] | null; zone?: string | null;
  /** /artist/ pages: the artist's Spotify, YouTube, Instagram... (artist_links) */
  artist_links?: { service: string; url: string }[] }

// The page the server rendered (div.ssr, replaced by the app) already has this address's title, intro and related
// links: shown until /landing-info answers, so the heading does not grow a line and the links do not pop in (layout shift).
const ssr = document.querySelector("#root > .ssr");
const ssrInfo = ssr && {
  path: location.pathname, lang: document.documentElement.lang,
  info: { title: ssr.querySelector("h1")?.textContent || null, intro: ssr.querySelector("h1 + p")?.textContent || null,
    links: [...ssr.querySelectorAll(":scope > nav a")].map((a) => ({ path: a.getAttribute("href") ?? "", label: a.textContent ?? "" })), country: null, city: null } as LandingInfo,
};

/** Title, intro and related links of a page, as the server renders them for crawlers. */
export const useLandingInfo = (path: string, lang: string) =>
  useQuery({
    queryKey: ["landing-info", path, lang],
    queryFn: () => getJSON<LandingInfo>(`/landing-info?path=${encodeURIComponent(path)}&lang=${lang}`),
    staleTime: 5 * 60_000,
    placeholderData: ssrInfo && ssrInfo.path === path && ssrInfo.lang === lang ? ssrInfo.info : undefined,
  });
