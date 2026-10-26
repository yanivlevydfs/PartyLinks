// "Add SPOTX to your home screen" banner, mobile only, 10 seconds after the page opens.
// Android (Chrome, Edge, Samsung): the browser's own install dialog via beforeinstallprompt.
// iOS has no such event: the banner shows the Share > Add to Home Screen steps instead.
import { useEffect, useState } from "react";
import { useConsent } from "./consent";
import { useT } from "./ui";

export const INSTALL_KEY = "partylinks-install-dismissed";
const DELAY_MS = 10_000;
const SNOOZE_DAYS = 14;

interface InstallPrompt extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

// Fires once, possibly before React mounts: catch it at module load.
let deferred: InstallPrompt | null = null;
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault(); // our banner instead of the browser's mini-infobar
  deferred = e as InstallPrompt;
});

const ua = navigator.userAgent;
const isIos = /iPhone|iPad|iPod/.test(ua) || (navigator.maxTouchPoints > 1 && /Macintosh/.test(ua));
const isMobile = isIos || /Android|Mobi/i.test(ua);
const standalone = () =>
  matchMedia("(display-mode: standalone)").matches || (navigator as { standalone?: boolean }).standalone === true;

function snoozed(): boolean {
  try {
    const at = Number(localStorage.getItem(INSTALL_KEY));
    return !!at && Date.now() - at < SNOOZE_DAYS * 86_400_000;
  } catch {
    return false;
  }
}

const TEXT = {
  he: {
    title: "התקינו את SPOTX",
    body: "כל האירועים במסך הבית, בלחיצה אחת ובמסך מלא.",
    ios: "לחצו על שיתוף ואז על \"הוספה למסך הבית\".",
    install: "התקנה",
    later: "לא עכשיו",
  },
  en: {
    title: "Install SPOTX",
    body: "Every event on your home screen, one tap away, full screen.",
    ios: "Tap Share, then \"Add to Home Screen\".",
    install: "Install",
    later: "Not now",
  },
};

export function InstallBanner() {
  const { lang } = useT();
  const t = TEXT[lang];
  const consentOpen = useConsent((s) => s.panel !== null);
  const [due, setDue] = useState(false);
  const [closed, setClosed] = useState(false);

  useEffect(() => {
    if (!isMobile || standalone() || snoozed()) return;
    const timer = setTimeout(() => setDue(true), DELAY_MS);
    const installed = () => setClosed(true);
    window.addEventListener("appinstalled", installed);
    return () => { clearTimeout(timer); window.removeEventListener("appinstalled", installed); };
  }, []);

  // Never on top of the consent banner; Android only once the browser says the app is installable.
  if (!due || closed || consentOpen || (!isIos && !deferred)) return null;

  const dismiss = () => {
    try { localStorage.setItem(INSTALL_KEY, String(Date.now())); } catch {}
    setClosed(true);
  };
  const install = async () => {
    const prompt = deferred;
    deferred = null; // a prompt can be used once
    if (!prompt) return;
    await prompt.prompt();
    if ((await prompt.userChoice).outcome === "dismissed") dismiss();
    else setClosed(true);
  };

  return (
    <aside className="install" role="dialog" aria-modal="false" aria-labelledby="install-title">
      <img src="/icon-192.png?v=spotx2" alt="" width="48" height="48" />
      <div className="install-txt">
        <b id="install-title">{t.title}</b>
        <span>{isIos ? <>{t.ios} <ShareIcon /></> : t.body}</span>
      </div>
      <div className="install-btns">
        {!isIos && <button className="c-btn" onClick={install}>{t.install}</button>}
        <button className="c-btn ghost" onClick={dismiss}>{t.later}</button>
      </div>
    </aside>
  );
}

function ShareIcon() {
  return (
    <svg className="install-share" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 3v12M7.5 7.5 12 3l4.5 4.5M6 11H5v10h14V11h-1" />
    </svg>
  );
}
