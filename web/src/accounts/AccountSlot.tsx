// The header's account corner: "Sign in" (the Google / Facebook choice) when signed out, the avatar menu
// when signed in, nothing when sign-in is off. Plain links to our server, which redirects to Google or
// Facebook: no Google or Facebook script.
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { BellMenuItem } from "../alerts/Bell";
import { useBell } from "../alerts/useAlerts";
import { SavedMenuItem } from "../saved/SavedLink";
import { useStore } from "../store";
import { useAccountText } from "./i18n";
import { SignInSheet } from "./SignInSheet";
import { signInUrl, useAccountsOn, useMe, useProviders, useSignOut } from "./useMe";
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

/** Facebook's "f", drawn inline (Facebook's login button branding). */
function FacebookF() {
  return (
    <svg className="g-logo" viewBox="0 0 24 24" aria-hidden width="20" height="20">
      <path fill="#fff" d="M24 12.07C24 5.4 18.63 0 12 0S0 5.4 0 12.07C0 18.1 4.39 23.1 10.13 24v-8.44H7.08v-3.49h3.05V9.41c0-3.02 1.79-4.69 4.53-4.69 1.31 0 2.68.24 2.68.24v2.97h-1.51c-1.49 0-1.96.93-1.96 1.89v2.25h3.33l-.53 3.49h-2.8V24C19.61 23.1 24 18.1 24 12.07z" />
    </svg>
  );
}

/** One button per configured provider ("Continue with Google", "Continue with Facebook"). */
export function SignInButton({ next, label }: { next?: string; label?: string }) {
  const t = useAccountText();
  const providers = useProviders();
  return (
    <div className="signin-btns">
      {providers.includes("google") && (
        <a className="google-btn" href={signInUrl(next)} rel="nofollow">
          <GoogleG /> <span>{label ?? t.google}</span>
        </a>
      )}
      {providers.includes("facebook") && (
        <a className="google-btn fb-btn" href={signInUrl(next, "facebook")} rel="nofollow">
          <FacebookF /> <span>{t.facebook}</span>
        </a>
      )}
    </div>
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
  const unseen = useBell().data?.unseen ?? 0;
  const lang = useStore((s) => s.lang);
  const setLang = useStore((s) => s.setLang);
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
        {unseen > 0 && <b className="menu-phone avatar-dot" aria-hidden />}
      </button>
      {open && (
        <div className="user-pop" role="menu">
          <div className="user-who"><b dir="auto">{me.name}</b><small dir="ltr">{me.email}</small></div>
          <BellMenuItem onClick={close} />
          <SavedMenuItem onClick={close} />
          <Link role="menuitem" to="/account" onClick={close}>{t.account}</Link>
          <Link role="menuitem" to="/groups" onClick={close}><span aria-hidden>👥</span> {t.groups}</Link>
          {me.role === "admin" && <Link role="menuitem" to="/admin" onClick={close}>{t.admin}</Link>}
          <button role="menuitem" className="menu-phone" onClick={() => { close(); setLang(lang === "he" ? "en" : "he"); }}>
            🌐 {lang === "he" ? "English" : "עברית"}
          </button>
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
  const [choosing, setChoosing] = useState(false);
  if (!on || me.isLoading) return null;
  return (
    <>
      {me.data ? <UserMenu /> : (
        <button type="button" className="sign-in" aria-haspopup="dialog" aria-label={t.signInTitle} onClick={() => setChoosing(true)}>
          <svg className="g-logo" viewBox="0 0 24 24" aria-hidden width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="8" r="4" /><path d="M4 21c0-4 3.6-7 8-7s8 3 8 7" />
          </svg>
          <span>{t.signIn}</span>
        </button>
      )}
      {choosing && (
        <SignInSheet title={t.signInTitle} text="" next={location.pathname + location.search} onClose={() => setChoosing(false)} />
      )}
      <WelcomeSheet />
    </>
  );
}
