// "Sign in to save": the popup a signed-out visitor gets on a personal action (the save bell on an event,
// "Save as alert"). Continue with Google comes back to ``next``, where the action completes.
// Drawn at the page's root (a portal): a card moves on hover, which would drag a popup inside it.
import { useEffect } from "react";
import { createPortal } from "react-dom";
import { SignInButton } from "./AccountSlot";
import { useAccountText } from "./i18n";
import "./accounts.css";

export function SignInSheet({ title, text, next, onClose }: { title: string; text: string; next: string; onClose: () => void }) {
  const t = useAccountText();
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [onClose]);
  return createPortal(
    <div className="sheet-back" onClick={(e) => { e.stopPropagation(); onClose(); }}>
      <div className="sheet" role="dialog" aria-modal="true" aria-labelledby="signin-title" onClick={(e) => e.stopPropagation()}>
        <h2 id="signin-title">{title}</h2>
        <p>{text}</p>
        <SignInButton next={next} />
        <p className="muted sheet-small">{t.why}</p>
        <button type="button" className="link-btn" onClick={onClose} autoFocus>{t.close}</button>
      </div>
    </div>,
    document.body,
  );
}

/** This page's address with ``param=value`` added: where Google sends the visitor back. */
export function returnTo(param: string, value: string) {
  const url = new URL(location.href);
  url.searchParams.set(param, value);
  return url.pathname + url.search;
}
