// /account: name, email language, delete account. Signed out: what an account adds, and the Google button.
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Hero, Loader, useTitle } from "../ui";
import { SignInButton } from "./AccountSlot";
import { useAccountText } from "./i18n";
import { send, useMe, type Me } from "./useMe";
import "./accounts.css";

export function useNoIndex() {
  useEffect(() => {
    const meta = document.createElement("meta");
    meta.name = "robots";
    meta.content = "noindex, nofollow";
    document.head.appendChild(meta);
    return () => meta.remove();
  }, []);
}

function Profile({ me }: { me: Me }) {
  const t = useAccountText();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [name, setName] = useState(me.name);
  const [locale, setLocale] = useState(me.locale);
  const [note, setNote] = useState("");
  const [confirm, setConfirm] = useState("");

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      qc.setQueryData(["me"], await send<Me>("/api/me", "PATCH", { name, locale }));
      setNote(t.savedOk);
    } catch {
      setNote(t.failed);
    }
  };
  // The digest is advertising (ticket links): only on the user's own tick, saved at once with its time.
  const toggleDigest = async (on: boolean) => {
    try {
      qc.setQueryData(["me"], await send<Me>("/api/me", "PATCH", { digest_opt_in: on }));
      setNote(t.savedOk);
    } catch {
      setNote(t.failed);
    }
  };
  const remove = async () => {
    try {
      await send("/api/me", "DELETE");
      qc.setQueryData(["me"], null);
      navigate("/");
    } catch {
      setNote(t.failed);
    }
  };

  return (
    <>
      <form className="acc-form" onSubmit={save}>
        <label>{t.name}<input value={name} maxLength={100} onChange={(e) => setName(e.target.value)} dir="auto" required /></label>
        <label>{t.email}<input value={me.email} readOnly dir="ltr" /></label>
        <label>{t.emailLang}
          <select value={locale} onChange={(e) => setLocale(e.target.value as Me["locale"])}>
            <option value="he">עברית</option>
            <option value="en">English</option>
          </select>
        </label>
        <button className="c-btn">{t.save}</button>
        <p className="muted" role="status">{note}</p>
      </form>
      <section className="acc-form">
        <label className="acc-check">
          <input type="checkbox" checked={me.digest_opt_in} onChange={() => toggleDigest(!me.digest_opt_in)} />
          <span>{t.digest}</span>
        </label>
        <p className="muted">{t.digestNote}</p>
      </section>
      <section className="acc-danger">
        <h2>{t.deleteTitle}</h2>
        <p>{t.deleteText}</p>
        <label>{t.deleteType}<input value={confirm} onChange={(e) => setConfirm(e.target.value)} dir="auto" /></label>
        <button className="c-btn danger" disabled={confirm.trim().toLowerCase() !== t.deleteWord} onClick={remove}>{t.deleteButton}</button>
      </section>
    </>
  );
}

export default function AccountPage() {
  const t = useAccountText();
  const me = useMe();
  useTitle(t.account);
  useNoIndex();
  return (
    <>
      <Hero title={t.account} />
      <main className="wrap statement">
        <p>{t.why}</p>
        {me.isLoading ? <Loader /> : me.data ? <Profile me={me.data} /> : <SignInButton next="/account" />}
      </main>
    </>
  );
}
