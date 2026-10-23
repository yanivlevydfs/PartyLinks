import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import type { Categories, EventDetail, EventPage, MapPins, Post, PostList } from "./types";

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
    getNextPageParam: (last) => (last.page * last.page_size < last.total ? last.page + 1 : undefined),
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

export const usePosts = (category?: string) =>
  useQuery({ queryKey: ["posts", category], queryFn: () => getJSON<PostList>(`/posts${category ? `?category=${encodeURIComponent(category)}` : ""}`) });

export const usePost = (slug: string) =>
  useQuery({ queryKey: ["post", slug], queryFn: () => getJSON<Post>(`/posts/${encodeURIComponent(slug)}`) });
