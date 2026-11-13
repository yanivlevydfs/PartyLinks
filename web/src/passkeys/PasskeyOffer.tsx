// After a Google / Facebook sign-in, once per device: "Sign in with your fingerprint next time?". Not on a
// device that already has one (device.ts), not inside Telegram, not over the first-sign-in welcome, and
// "Not now" waits site.passkey_offer_days (the admin's config tab; 0 never offers).
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useMe } from "../accounts/useMe";
import { TG_APP } from "../bot/telegramApp";
import { hasHere, snooze, snoozed } from "./device";
import { usePasskeyText } from "./i18n";
import { builtIn, usePasskeyActions } from "./passkeys";
import "./passkeys.css";

export function PasskeyOffer() {
  const t = usePasskeyText();
  const me = useMe().data;
  const days = useQuery({ queryKey: ["site"], queryFn: () => fetch("/site").then((r) => r.json() as Promise<{ passkey_offer_days?: number }>) })
    .data?.passkey_offer_days;
  const { add } = usePasskeyActions();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  useEffect(() => {
    const welcome = /[?&](welcome|signin)=/.test(location.search);
    if (!me || !days || TG_APP || welcome || hasHere() || snoozed(days)) return;
    let live = true;
    void builtIn().then((ok) => live && setOpen(ok));
    return () => { live = false; };
  }, [me, days]);
  if (!open) return null;
  const later = () => { snooze(); setOpen(false); };
  const yes = async () => {
    setBusy(true);
    const failed = await add();
    setBusy(false);
    if (failed === null || failed === "already") return setOpen(false);
    if (failed === "cancelled") return later();
    setNote(t.failed);
  };
  return createPortal(
    <div className="sheet-back" onClick={later}>
      <div className="sheet pk-offer" role="dialog" aria-modal="true" aria-labelledby="pk-offer-title" onClick={(e) => e.stopPropagation()}>
        <h2 id="pk-offer-title">{t.offerTitle}</h2>
        <p>{t.offerText}</p>
        <div className="pk-actions">
          <button type="button" className="c-btn" onClick={yes} disabled={busy} aria-busy={busy}>{busy ? t.working : t.offerYes}</button>
          <button type="button" className="link-btn" onClick={later} disabled={busy}>{t.offerNo}</button>
        </div>
        {note && <p className="pk-error" role="alert">{note}</p>}
      </div>
    </div>,
    document.body, // the header (backdrop-filter) would pin a fixed sheet to its own box
  );
}
