// "SPOTX on Telegram": the bot's banner on the home page. Drawn in CSS (no picture: no extra request,
// see the rate-limit note in memory), shown only while the bot runs (GET /site → telegram, its @name),
// and hidden for 30 days once closed.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useT } from "../ui";
import "./telegram-promo.css";

const KEY = "partylinks-telegram-promo-closed";
const SNOOZE_DAYS = 30;

const TEXT = {
  he: {
    kicker: "חדש · בוט טלגרם",
    title: "כל המסיבות בצ׳אט אחד",
    body: "כתבו מה בא לכם ותקבלו אירועים עם קישור לכרטיסים. תזכורות לפני, התראות על אירועים חדשים ומעקב מחיר.",
    chips: ["🎵 קטגוריות", "📍 אזורים", "⏰ תזכורות", "🔔 התראות"],
    cta: "פתיחה בטלגרם",
    free: "חינם · בלי הרשמה",
    close: "סגירה",
    me: "טכנו בתל אביב הערב?",
    bot: "מצאתי 12 מסיבות 🎉",
    card: "Techno Bunker · 23:00",
  },
  en: {
    kicker: "New · Telegram bot",
    title: "Every party in one chat",
    body: "Write what you're up for and get events with ticket links. Reminders before, alerts on new events and price watch.",
    chips: ["🎵 Categories", "📍 Areas", "⏰ Reminders", "🔔 Alerts"],
    cta: "Open in Telegram",
    free: "Free · No sign-up",
    close: "Close",
    me: "Techno in Tel Aviv tonight?",
    bot: "Found 12 parties 🎉",
    card: "Techno Bunker · 23:00",
  },
};

function closed(): boolean {
  try {
    const at = Number(localStorage.getItem(KEY));
    return !!at && Date.now() - at < SNOOZE_DAYS * 86_400_000;
  } catch {
    return false;
  }
}

function TelegramIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="tg-icon">
      <path fill="currentColor" d="M9.78 15.17 9.6 19.3c.36 0 .52-.16.71-.35l1.7-1.63 3.53 2.58c.65.36 1.11.17 1.29-.6l2.33-10.94c.21-.96-.35-1.34-.98-1.1L4.5 12.53c-.94.37-.92.9-.16 1.13l3.5 1.09 8.13-5.13c.38-.25.73-.11.44.14Z" />
    </svg>
  );
}

export function TelegramPromo() {
  const { lang } = useT();
  const t = TEXT[lang];
  const name = useQuery({
    queryKey: ["site"],
    queryFn: () => fetch("/site").then((r) => r.json() as Promise<{ telegram?: string | null }>),
  }).data?.telegram;
  const [hidden, setHidden] = useState(closed);
  if (!name || hidden) return null;
  const close = () => {
    try {
      localStorage.setItem(KEY, String(Date.now()));
    } catch {}
    setHidden(true);
  };
  const href = `https://t.me/${name}?start=site`;

  return (
    <aside className="tg-promo" aria-label={t.kicker}>
      <button className="tg-close" onClick={close} aria-label={t.close}>×</button>
      <div className="tg-text">
        <span className="tg-kicker"><TelegramIcon /> {t.kicker}</span>
        <h2 className="tg-title">{t.title}</h2>
        <p className="tg-body">{t.body}</p>
        <div className="tg-chips">{t.chips.map((c) => <span key={c}>{c}</span>)}</div>
        <div className="tg-actions">
          <a className="tg-cta" href={href} target="_blank" rel="noopener">
            <TelegramIcon /> {t.cta}
          </a>
          <span className="tg-free">{t.free} · <bdi dir="ltr">@{name}</bdi></span>
        </div>
      </div>
      {/* A chat in miniature: what the bot does, at a glance. Decorative. */}
      <div className="tg-phone" aria-hidden="true">
        <div className="tg-bubble tg-me">{t.me}</div>
        <div className="tg-bubble tg-bot">{t.bot}</div>
        <div className="tg-card">
          <div className="tg-card-img" />
          <b>{t.card}</b>
          <span className="tg-card-btn">🎟</span>
        </div>
      </div>
    </aside>
  );
}
