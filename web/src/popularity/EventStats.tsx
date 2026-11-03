// How many liked / viewed / are watching an event. The numbers come with the event (no request of
// their own); a zero is left out, so a new event shows nothing.
import type { EventDetail, EventSummary } from "../types";
import { usePopularityText } from "./i18n";
import "./popularity.css";

export function EventStats({ e, compact = false }: { e: EventSummary & Partial<Pick<EventDetail, "watching">>; compact?: boolean }) {
  const t = usePopularityText();
  const n = (x: number) => x.toLocaleString(t.locale, compact ? { notation: "compact" } : undefined);
  const items = [
    !!e.watching && <span key="w" className="pop-live">{t.watching(n(e.watching))}</span>,
    !!e.likes && <span key="l">♥ {t.likes(n(e.likes))}</span>,
    !!e.views && <span key="v">👁 {t.views(n(e.views))}</span>,
  ].filter(Boolean);
  if (!items.length) return null;
  return <p className={compact ? "meta pop" : "pop pop-large"}>{items}</p>;
}
