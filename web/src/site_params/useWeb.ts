// The website's tunable numbers (site.web in the admin's config tab, sent by GET /site → web), read from
// the ["site"] query other parts share: no extra request. Until it loads, the defaults below (the same
// values as config.py WebSettings) apply.
import { useQuery } from "@tanstack/react-query";

export const WEB_DEFAULTS = {
  events_page_size: 24,
  index_page_size: 60,
  home_row_events: 12,
  map_popup_events: 8,
  home_search_debounce_ms: 250,
  search_debounce_ms: 300,
  card_lineup_shown: 4,
  card_genres_shown: 3,
  footer_genres: 12,
  footer_min_events: 3,
  install_delay_seconds: 10,
  install_snooze_days: 14,
  telegram_bubble_snooze_days: 30,
  bell_poll_seconds: 60,
  plan_poll_seconds: 10,
  saved_local_max: 500,
};
export type WebParams = typeof WEB_DEFAULTS;

export function useWeb(): WebParams {
  const web = useQuery({
    queryKey: ["site"],
    queryFn: () => fetch("/site").then((r) => r.json() as Promise<{ web?: Partial<WebParams> }>),
  }).data?.web;
  return { ...WEB_DEFAULTS, ...web };
}
