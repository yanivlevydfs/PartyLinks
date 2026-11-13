// What this browser knows about its own passkey for the site (the server can't tell which device holds one):
// "here" once one was added or used here, cleared when the server no longer knows it; "snooze" is when
// "Not now" was pressed on the offer (PasskeyOffer.tsx). localStorage only: private mode just asks again.
const HERE = "spotx-passkey-here";
const SNOOZE = "spotx-passkey-snooze";

function get(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}
function set(key: string, value: string | null) {
  try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value); } catch { /* blocked: ask again */ }
}

export const hasHere = () => get(HERE) === "1";
export const markHere = (yes: boolean) => set(HERE, yes ? "1" : null);
export const snooze = () => set(SNOOZE, String(Date.now()));
export const snoozed = (days: number) => Date.now() - Number(get(SNOOZE) ?? 0) < days * 86_400_000;
