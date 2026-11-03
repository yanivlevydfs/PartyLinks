// "Share SPOTX" in the footer: shares the site itself, not one event.
import { useState, type ReactNode } from "react";
import { SHARE_ICONS, useT } from "../ui";
import "./share.css";

const SITE = "https://spotx.fun/";

const TEXT = {
  he: {
    label: "אהבתם? שתפו את SPOTX עם חברים",
    message: "SPOTX: כל המסיבות, ההופעות והפסטיבלים בישראל במקום אחד",
    shareOn: (name: string) => `שתפו את SPOTX ב־${name}`,
    native: "שיתוף SPOTX",
    copy: "העתקת הקישור לאתר",
    copied: "הקישור הועתק",
  },
  en: {
    label: "Like it? Share SPOTX with friends",
    message: "SPOTX: every party, gig and festival in Israel in one place",
    shareOn: (name: string) => `Share SPOTX on ${name}`,
    native: "Share SPOTX",
    copy: "Copy the site link",
    copied: "Link copied",
  },
};

const TELEGRAM = <path d="M21.4 3.6 2.9 10.8c-1.2.5-1.2 1.2-.2 1.5l4.7 1.5 1.8 5.6c.2.6.4.8.9.8.4 0 .6-.2.9-.5l2.3-2.2 4.8 3.5c.9.5 1.5.2 1.7-.8l3.1-14.8c.3-1.3-.5-1.9-1.5-1.4zM9.6 14.3l-.4 4 -1.4-4.6 10.4-6.6z" />;
const COPY = (
  <g fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <rect x="8" y="8" width="12" height="12" rx="2.5" /><path d="M16 8V6.5A2.5 2.5 0 0 0 13.5 4h-7A2.5 2.5 0 0 0 4 6.5v7A2.5 2.5 0 0 0 6.5 16H8" />
  </g>
);
const CHECK = <path d="m5 12.5 4.5 4.5L19 7.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />;
const SHARE = (
  <g fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="18" cy="5" r="2.5" /><circle cx="6" cy="12" r="2.5" /><circle cx="18" cy="19" r="2.5" /><path d="m8.2 10.8 7.6-4.4M8.2 13.2l7.6 4.4" />
  </g>
);

export function SiteShare() {
  const { lang } = useT();
  const [copied, setCopied] = useState(false);
  const s = TEXT[lang === "en" ? "en" : "he"];
  const url = encodeURIComponent(SITE);
  const text = encodeURIComponent(s.message);
  const targets: [string, string, string, ReactNode][] = [
    ["whatsapp", "WhatsApp", `https://api.whatsapp.com/send?text=${text}%20${url}`, SHARE_ICONS.whatsapp],
    ["telegram", "Telegram", `https://t.me/share/url?url=${url}&text=${text}`, TELEGRAM],
    ["facebook", "Facebook", `https://www.facebook.com/sharer/sharer.php?u=${url}`, SHARE_ICONS.facebook],
    ["x", "X", `https://x.com/intent/post?text=${text}&url=${url}`, SHARE_ICONS.x],
  ];
  const copy = () =>
    navigator.clipboard?.writeText(SITE).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }, () => {});
  return (
    <div className="site-share">
      <p className="site-share-label">{s.label}</p>
      <div className="share">
        {typeof navigator.share === "function" && (
          <button type="button" className="share-btn native" onClick={() => navigator.share({ title: "SPOTX", text: s.message, url: SITE }).catch(() => {})} aria-label={s.native} title={s.native}>
            <svg viewBox="0 0 24 24" aria-hidden>{SHARE}</svg>
          </button>
        )}
        {targets.map(([key, name, href, icon]) => (
          <a key={key} className={"share-btn " + key} href={href} target="_blank" rel="noopener noreferrer" aria-label={s.shareOn(name)} title={s.shareOn(name)}>
            <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden>{icon}</svg>
          </a>
        ))}
        <button type="button" className="share-btn copy" onClick={copy} aria-label={copied ? s.copied : s.copy} title={copied ? s.copied : s.copy}>
          <svg viewBox="0 0 24 24" aria-hidden>{copied ? CHECK : COPY}</svg>
        </button>
        <span className="sr-only" role="status">{copied ? s.copied : ""}</span>
      </div>
    </div>
  );
}
