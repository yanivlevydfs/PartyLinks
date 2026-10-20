import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import type { Categories, EventDetail, EventPage, EventSummary, Post, PostList } from "./types";

export type Params = Record<string, string | string[] | undefined>;

const PAGE_SIZE = 24;
const MAX_PAGE_SIZE = 200; // api.max_page_size in config/settings.yaml

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

/** Every upcoming event (all pages), for the map. */
export function useAllEvents() {
  return useQuery({
    queryKey: ["events", "all"],
    queryFn: async () => {
      const items: EventSummary[] = [];
      for (let page = 1; ; page++) {
        const d = await getJSON<EventPage>(`/events?page_size=${MAX_PAGE_SIZE}&page=${page}`);
        items.push(...d.items);
        if (page * d.page_size >= d.total) return items;
      }
    },
  });
}

export const useEvent = (id: string) =>
  useQuery({ queryKey: ["event", id], queryFn: () => getJSON<EventDetail>(`/events/${encodeURIComponent(id)}`) });

export const useCategories = () =>
  useQuery({ queryKey: ["categories"], queryFn: () => getJSON<Categories>("/categories"), staleTime: 5 * 60_000 });

export const usePosts = (category?: string) =>
  useQuery({ queryKey: ["posts", category], queryFn: () => getJSON<PostList>(`/posts${category ? `?category=${encodeURIComponent(category)}` : ""}`) });

export const usePost = (slug: string) =>
  useQuery({ queryKey: ["post", slug], queryFn: () => getJSON<Post>(`/posts/${encodeURIComponent(slug)}`) });
