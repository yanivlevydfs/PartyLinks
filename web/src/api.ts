import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import type { Categories, EventDetail, EventPage, EventSummary, MapPins, Post, PostList, SponsoredRails } from "./types";

export type Params = Record<string, string | string[] | undefined>;

const PAGE_SIZE = 24;

async function getJSON<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error(`${res.status} ${url}`);
  return res.json();
}

export function toQuery(params: Params): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    for (const item of Array.isArray(v) ? v : [v]) if (item) qs.append(k, item);
  }
  return qs.toString();
}

export function useEvents(params: Params) {
  const qs = toQuery({ page_size: String(PAGE_SIZE), ...params });
  return useInfiniteQuery({
    queryKey: ["events", qs],
    // signal: a request overtaken by the next keystroke or filter is cancelled, not left to load the server.
    queryFn: ({ pageParam, signal }) => getJSON<EventPage>(`/events?${qs}&page=${pageParam}`, signal),
    placeholderData: (prev) => prev, // the old results stay (dimmed) while the new ones load: no flash of skeletons
    initialPageParam: 1,
    getNextPageParam: (last) => (last.page < 40 && last.page * last.page_size < last.total ? last.page + 1 : undefined), // 40: the API's max_page
  });
}

/** Every event matching the filter that has coordinates, in one request, for the map. */
export function useMapEvents(params: Params = {}) {
  const qs = toQuery(params);
  return useQuery({ queryKey: ["events", "map", qs], queryFn: () => getJSON<MapPins>(`/events/map?${qs}`) });
}

export const useEvent = (id: string) =>
  useQuery({ queryKey: ["event", id], queryFn: () => getJSON<EventDetail>(`/events/${encodeURIComponent(id)}`) });

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

export const usePosts = (lang: string, category?: string) =>
  useQuery({
    queryKey: ["posts", lang, category],
    queryFn: () => getJSON<PostList>(`/posts?lang=${lang}${category ? `&category=${encodeURIComponent(category)}` : ""}`),
  });

/** A query kept in localStorage too: a return visit draws the last copy at once while a fresh one loads. */
function useKept<T>(key: string, url: string) {
  const store = `q:${key}`;
  return useQuery({
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

/** The home page's rows under "Trending": a pool of the busiest events per row (server-side HOME_RAILS). */
export const useRails = () => useKept<{ rails: Record<string, EventSummary[]> }>("rails", "/events/rails");

export interface TicketSite { name: string; events: number; updated_at: string | null }

/** The stores page: upcoming events per ticket site, with its last scrape. */
export const useTicketSites = () =>
  useQuery({ queryKey: ["ticket-sites"], queryFn: () => getJSON<{ stores: TicketSite[] }>("/ticket-sites"), staleTime: 5 * 60_000 });

export const usePost = (slug: string, lang: string) =>
  useQuery({ queryKey: ["post", slug, lang], queryFn: () => getJSON<Post>(`/posts/${encodeURIComponent(slug)}?lang=${lang}`) });

export interface LandingInfo { title: string | null; intro: string | null; links: { path: string; label: string }[]; country: string | null; city: { he: string; en: string } | null }

/** Title, intro and related links of a page, as the server renders them for crawlers. */
export const useLandingInfo = (path: string, lang: string) =>
  useQuery({
    queryKey: ["landing-info", path, lang],
    queryFn: () => getJSON<LandingInfo>(`/landing-info?path=${encodeURIComponent(path)}&lang=${lang}`),
    staleTime: 5 * 60_000,
  });
