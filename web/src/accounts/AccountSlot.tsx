// The header's account corner: "Continue with Google" when signed out, the avatar menu when signed in,
// nothing when sign-in is off. A plain link to our server, which redirects to Google: no Google script.
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { SavedMenuItem } from "../saved/SavedLink";
import { useAccountText } from "./i18n";
import { signInUrl, useAccountsOn, useMe, useSignOut } from "./useMe";
import { WelcomeSheet } from "./WelcomeSheet";
import "./accounts.css";

/** Google's "G", drawn inline (Google's sign-in branding). */
export function GoogleG() {
  return (
    <svg className="g-logo" viewBox="0 0 48 48" aria-hidden width="18" height="18">
      <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.6 5.4 2.7 13.3l7.9 6.1C12.5 13.6 17.8 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.1 24.6c0-1.6-.1-3.1-.4-4.6H24v9h12.4c-.5 2.9-2.2 5.3-4.6 6.9l7.4 5.8c4.3-4 6.9-9.9 6.9-17.1z" />
      <path fill="#FBBC05" d="M10.6 28.6c-.5-1.4-.8-3-.8-4.6s.3-3.2.8-4.6l-7.9-6.1C1 16.6 0 20.2 0 24s1 7.4 2.7 10.7l7.9-6.1z" />
      <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.4-5.8c-2.1 1.4-4.8 2.3-8.5 2.3-6.2 0-11.5-4.1-13.4-9.9l-7.9 6.1C6.6 42.6 14.6 48 24 48z" />
    </svg>
  );
}

export function SignInButton({ next, label }: { next?: string; label?: string }) {
  const t = useAccountText();
  return (
    <a className="google-btn" href={signInUrl(next)} rel="nofollow">
      <GoogleG /> <span>{label ?? t.google}</span>
    </a>
  );
}

function Avatar({ name, picture }: { name: string; picture: string | null }) {
  const [broken, setBroken] = useState(false);
  if (picture && !broken)
    return <img className="avatar" src={picture} alt="" width="32" height="32" referrerPolicy="no-referrer" onError={() => setBroken(true)} />;
  return <span className="avatar" aria-hidden>{(name.trim()[0] ?? "?").toUpperCase()}</span>;
}

function UserMenu() {
  const t = useAccountText();
  const me = useMe().data!;
  const signOut = useSignOut();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: Event) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("pointerdown", away); document.removeEventListener("keydown", esc); };
  }, [open]);
  const close = () => setOpen(false);
  return (
    <div className="user-menu" ref={box}>
      <button className="user-btn" aria-haspopup="menu" aria-expanded={open} aria-label={t.menu} onClick={() => setOpen(!open)}>
        <Avatar name={me.name} picture={me.picture} />
      </button>
      {open && (
        <div className="user-pop" role="menu">
          <div className="user-who"><b dir="auto">{me.name}</b><small dir="ltr">{me.email}</small></div>
          <SavedMenuItem onClick={close} />
          <Link role="menuitem" to="/account" onClick={close}>{t.account}</Link>
          <Link role="menuitem" to="/groups" onClick={close}>{t.groups}</Link>
          {me.role === "admin" && <Link role="menuitem" to="/admin" onClick={close}>{t.admin}</Link>}
          <button role="menuitem" onClick={() => { close(); void signOut(); }}>{t.signOut}</button>
        </div>
      )}
    </div>
  );
}

export function AccountSlot() {
  const on = useAccountsOn();
  const me = useMe();
  const t = useAccountText();
  if (!on || me.isLoading) return null;
  return (
    <>
      {me.data ? <UserMenu /> : (
        <a className="sign-in" href={signInUrl()} rel="nofollow" aria-label={t.google}>
          <GoogleG /><span>{t.signIn}</span>
        </a>
      )}
      <WelcomeSheet />
    </>
  );
}
