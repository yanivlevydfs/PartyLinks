// SPOTX inside Telegram (a Mini App: the bot's menu button and its 🗺 menu row open /?src=tg-app). Telegram's
// script is loaded only then (the flag stays for the tab's session, as the app navigates); a normal visitor
// loads nothing. No login: browsing never needs one. Inside the app the site tells Telegram it is ready, takes
// the full height, paints Telegram's bars in the site's colour, and opens outside links (tickets, maps, shares)
// in the browser with openLink: a link left to the webview replaces the app. Ticket clicks count as tg-app.
type WebApp = {
  initData: string;
  ready(): void;
  expand(): void;
  setHeaderColor(color: string): void;
  setBackgroundColor(color: string): void;
  openLink(url: string): void;
};
declare global {
  interface Window { Telegram?: { WebApp?: WebApp } }
}

const KEY = "partylinks-tg-app";
const BG = "#06040b"; // --bg in styles.css

function flagged(): boolean {
  try {
    if (new URLSearchParams(location.search).get("src") === "tg-app") sessionStorage.setItem(KEY, "1");
    return sessionStorage.getItem(KEY) === "1";
  } catch {
    return new URLSearchParams(location.search).get("src") === "tg-app";
  }
}

/** Opened from the bot (TelegramFab hides itself: pointless inside Telegram). */
export const TG_APP = flagged();

if (TG_APP) {
  const script = document.createElement("script");
  script.src = "https://telegram.org/js/telegram-web-app.js";
  script.onload = () => {
    const app = window.Telegram?.WebApp;
    if (!app?.initData) return; // the URL alone, outside Telegram
    app.ready();
    app.expand();
    app.setHeaderColor(BG);
    app.setBackgroundColor(BG);
    document.addEventListener("click", (e) => {
      const a = e.target instanceof Element ? e.target.closest<HTMLAnchorElement>("a[target=_blank]") : null;
      if (e.defaultPrevented || !a || !/^https?:/.test(a.href)) return;
      const url = new URL(a.href);
      if (url.origin === location.origin && url.pathname.startsWith("/go/")) url.searchParams.set("at", "tg-app");
      e.preventDefault();
      app.openLink(url.href);
    });
  };
  document.head.appendChild(script);
}
