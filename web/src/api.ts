import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import type { Categories, EventDetail, EventPage, MapPins, Post, PostList, SponsoredRails } from "./types";

export type Params = Record<string, string | string[] | undefined>;

const PAGE_SIZE = 24;

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url);
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
    queryFn: ({ pageParam }) => getJSON<EventPage>(`/events?${qs}&page=${pageParam}`),
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

/** The home page's paid side rails (picked in the admin). */
export const useSponsored = () =>
  useQuery({ queryKey: ["sponsored"], queryFn: () => getJSON<SponsoredRails>("/events/sponsored"), staleTime: 5 * 60_000 });

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
