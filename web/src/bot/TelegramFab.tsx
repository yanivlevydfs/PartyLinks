// A floating "ask the bot" button on every page (it replaced the home page's Telegram banner), bottom-right (the accessibility button has bottom-left).
// Shown only while the bot runs (GET /site → telegram, the ["site"] query other parts share: no extra request).
// The speech bubble beside it can be closed, and stays closed for 30 days; the button itself stays.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useLocation } from "react-router-dom";
import { useT } from "../ui";
import { TG_APP } from "./telegramApp";
import "./telegram-fab.css";

const KEY = "partylinks-telegram-fab-closed";
const SNOOZE_DAYS = 30;

const TEXT = {
  he: { bubble: "מחפשים מסיבה? שאלו את הבוט", label: "הבוט שלנו בטלגרם", close: "סגירה" },
  en: { bubble: "Looking for a party? Ask our bot", label: "Our Telegram bot", close: "Close" },
};

function closed(): boolean {
  try {
    const at = Number(localStorage.getItem(KEY));
    return !!at && Date.now() - at < SNOOZE_DAYS * 86_400_000;
  } catch {
    return false;
  }
}

export function TelegramFab() {
  const { lang } = useT();
  const t = TEXT[lang];
  const { pathname } = useLocation();
  const name = useQuery({
    queryKey: ["site"],
    queryFn: () => fetch("/site").then((r) => r.json() as Promise<{ telegram?: string | null }>),
  }).data?.telegram;
  const [bubble, setBubble] = useState(() => !closed());
  if (!name || TG_APP || pathname.startsWith("/admin")) return null;
  const hide = () => {
    try {
      localStorage.setItem(KEY, String(Date.now()));
    } catch {}
    setBubble(false);
  };

  return (
    <div className="tg-fab">
      {bubble && (
        <div className="tg-fab-bubble">
          <a href={`https://t.me/${name}?start=widget`} target="_blank" rel="noopener" onClick={hide}>{t.bubble}</a>
          <button type="button" onClick={hide} aria-label={t.close}>×</button>
        </div>
      )}
      <a className="tg-fab-btn" href={`https://t.me/${name}?start=widget`} target="_blank" rel="noopener" aria-label={t.label} title={t.label} onClick={hide}>
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path fill="currentColor" d="M9.78 15.17 9.6 19.3c.36 0 .52-.16.71-.35l1.7-1.63 3.53 2.58c.65.36 1.11.17 1.29-.6l2.33-10.94c.21-.96-.35-1.34-.98-1.1L4.5 12.53c-.94.37-.92.9-.16 1.13l3.5 1.09 8.13-5.13c.38-.25.73-.11.44.14Z" />
        </svg>
        {bubble && <span className="tg-fab-dot" aria-hidden="true" />}
      </a>
    </div>
  );
}
