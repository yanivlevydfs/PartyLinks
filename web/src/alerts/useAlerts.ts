// Alerts: the site's filters saved as a search (GET/POST/PATCH/DELETE /api/me/alerts), the bell and "For you".
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { send, useMe } from "../accounts/useMe";
import { genreLabel, type Lang } from "../i18n";
import type { Filters } from "../store";
import type { EventSummary } from "../types";
import { label, useT } from "../ui";

export interface Criteria {
  q: string;
  genres: string[];
  regions: string[];
  areas: string[];
  city: string | null;
  slots: string[];
  freeOnly: boolean;
  discountOnly: boolean;
  when: string;
}

export interface Alert {
  id: number;
  name: string;
  criteria: Criteria;
  instant: boolean;
  active: boolean;
  unseen: number;
}

const ONGOING = ["all", "weekend", "week", "month"];

/** The home filters as an alert: "tonight" and a single day become "any time" (an alert keeps looking). */
export function toCriteria(f: Filters): Criteria {
  const range = f.when.includes("~");
  return {
    q: f.q.trim().length >= 2 ? f.q.trim() : "",
    genres: f.genres, regions: f.regions, areas: f.areas, city: f.city, slots: f.slots,
    freeOnly: f.freeOnly, discountOnly: f.discountOnly,
    when: ONGOING.includes(f.when) || range ? f.when : "all",
  };
}

export const isEmpty = (c: Criteria) =>
  !c.q && !c.genres.length && !c.regions.length && !c.areas.length && !c.city && !c.slots.length && !c.freeOnly && !c.discountOnly;

/** A short name from the criteria, in the page's language ("Techno · Tel Aviv · Weekend"). */
export function useCriteriaName() {
  const { t, lang } = useT();
  return (c: Criteria) => criteriaWords(c, t, lang).slice(0, 4).join(" · ").slice(0, 80);
}

export function criteriaWords(c: Criteria, t: ReturnType<typeof useT>["t"], lang: Lang): string[] {
  const when = c.when === "weekend" ? t.weekend : c.when === "week" ? t.week : c.when === "month" ? t.month : c.when.includes("~") ? c.when.replace("~", " – ") : null;
  return [
    c.q,
    ...c.genres.map((g) => genreLabel(lang, g)),
    ...c.regions.map((r) => label(t.areas, r)),
    ...c.areas.map((a) => label(t.areas, a)),
    c.city ?? "",
    ...c.slots.map((s) => label(t.slots, s)),
    c.freeOnly ? t.freeOnly : "",
    c.discountOnly ? t.discountOnly : "",
    when ?? "",
  ].filter(Boolean);
}

export function useAlerts() {
  const me = useMe().data;
  return useQuery<{ items: Alert[]; max: number }>({
    queryKey: ["alerts", me?.id],
    queryFn: () => fetch("/api/me/alerts", { credentials: "same-origin" }).then((r) => r.json()),
    enabled: !!me,
  });
}

/** Create, edit, delete; every change refreshes the list, the bell and "For you". */
export function useAlertActions() {
  const qc = useQueryClient();
  const refresh = () => Promise.all(["alerts", "bell", "for-you"].map((k) => qc.invalidateQueries({ queryKey: [k] })));
  return {
    create: async (criteria: Criteria, name: string, instant = false) => {
      const made = await send<Alert>("/api/me/alerts", "POST", { criteria, name, instant });
      await refresh();
      return made;
    },
    edit: async (id: number, patch: Partial<Pick<Alert, "name" | "instant" | "active" | "criteria">>) => {
      await send(`/api/me/alerts/${id}`, "PATCH", patch);
      await refresh();
    },
    remove: async (id: number) => {
      await send(`/api/me/alerts/${id}`, "DELETE");
      await refresh();
    },
  };
}

export interface BellItem {
  event: EventSummary;
  alert: string;
  matched_at: string;
  seen: boolean;
}

export function useBell() {
  const me = useMe().data;
  return useQuery<{ unseen: number; items: BellItem[] }>({
    queryKey: ["bell", me?.id],
    queryFn: () => fetch("/api/me/bell", { credentials: "same-origin" }).then((r) => r.json()),
    enabled: !!me,
    refetchInterval: 60_000, // once a minute, and only while the tab is visible (rate limit)
    refetchIntervalInBackground: false,
  });
}

export function useForYou(on: boolean) {
  const me = useMe().data;
  return useQuery<{ items: EventSummary[] }>({
    queryKey: ["for-you", me?.id],
    queryFn: () => fetch("/api/me/for-you", { credentials: "same-origin" }).then((r) => r.json()),
    enabled: !!me && on,
    staleTime: 5 * 60_000,
  });
}

/** "N events match now" for criteria not saved yet. */
export function usePreview(criteria: Criteria) {
  const me = useMe().data;
  const key = JSON.stringify(criteria);
  return useQuery<{ total: number; items: EventSummary[] }>({
    queryKey: ["alert-preview", key],
    queryFn: () => send("/api/me/alerts/preview", "POST", criteria) as Promise<{ total: number; items: EventSummary[] }>,
    enabled: !!me && !isEmpty(criteria),
    staleTime: 60_000,
  });
}
