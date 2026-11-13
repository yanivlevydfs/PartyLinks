// The /contact form: POST /api/contact (partylinks.contact) saves the message for the admin's requests tab. No sign-in.
// Reuses the /promote form's styles. On any failure the email address below the form still works.
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import "../promote/promote.css";
import { useT } from "../ui";

const TEXT = {
  he: {
    legend: "שלחו לנו הודעה", name: "שם", email: "דוא\"ל לחזרה", subject: "נושא (לא חובה)",
    page: "קישור לאירוע או לעמוד (לא חובה)", message: "ההודעה", send: "שליחה", sending: "שולח...",
    thanks: "תודה! ההודעה נשלחה, נחזור אליכם לדוא\"ל שכתבתם.", another: "הודעה נוספת",
    tooMany: "נשלחו יותר מדי הודעות. נסו שוב בעוד דקה.", badEmail: "כתובת הדוא\"ל לא תקינה.",
    short: "ההודעה קצרה מדי.", error: "ההודעה לא נשלחה. נסו שוב או כתבו לנו לכתובת שלמטה.",
    privacy: "השם, הדוא\"ל וההודעה נשמרים אצלנו רק כדי לענות לכם, עד שנמחק אותם או שתבקשו מחיקה.",
  },
  en: {
    legend: "Send us a message", name: "Name", email: "Your email", subject: "Subject (optional)",
    page: "Link to the event or page (optional)", message: "Message", send: "Send", sending: "Sending...",
    thanks: "Thanks! Your message was sent; we'll reply to the email you gave.", another: "Another message",
    tooMany: "Too many messages. Try again in a minute.", badEmail: "That email address doesn't look right.",
    short: "The message is too short.", error: "The message wasn't sent. Try again, or email us at the address below.",
    privacy: "Your name, email and message are kept only to answer you, until we delete them or you ask us to.",
  },
};

export function ContactForm() {
  const { lang } = useT();
  const t = TEXT[lang];
  const [sending, setSending] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");

  async function submit(ev: FormEvent<HTMLFormElement>) {
    ev.preventDefault();
    const form = new FormData(ev.currentTarget);
    const body = Object.fromEntries(["name", "email", "subject", "page", "message", "website"].map((k) => [k, form.get(k) ?? ""]));
    setSending(true);
    setError("");
    try {
      const res = await fetch("/api/contact", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Requested-With": "spotx" },
        body: JSON.stringify({ ...body, lang }),
      });
      if (res.ok) return setDone(true);
      const detail = JSON.stringify((await res.json().catch(() => null))?.detail ?? "");
      setError(res.status === 429 ? t.tooMany : detail.includes("email") ? t.badEmail : detail.includes("message") ? t.short : t.error);
    } catch {
      setError(t.error);
    } finally {
      setSending(false);
    }
  }

  if (done)
    return (
      <div className="pr" role="status">
        <p>{t.thanks}</p>
        <p className="pr-actions"><button type="button" className="c-btn" onClick={() => setDone(false)}>{t.another}</button></p>
      </div>
    );

  return (
    <form className="pr" onSubmit={submit}>
      <fieldset>
        <legend>{t.legend}</legend>
        <div className="pr-fields">
          <label>{t.name}<input className="pr-input" name="name" required minLength={2} maxLength={120} autoComplete="name" /></label>
          <label>{t.email}<input className="pr-input" name="email" type="email" required maxLength={254} autoComplete="email" dir="ltr" /></label>
          <label>{t.subject}<input className="pr-input" name="subject" maxLength={160} /></label>
          <label>{t.page}<input className="pr-input" name="page" type="url" maxLength={500} dir="ltr" placeholder="https://spotx.fun/event/..." /></label>
        </div>
        <label className="pr-wide">{t.message}
          <textarea className="pr-input" name="message" rows={6} required minLength={5} maxLength={3000} />
        </label>
        {/* Hidden from people; a bot that fills it is thanked and nothing is sent. */}
        <input className="pr-hp" name="website" tabIndex={-1} autoComplete="off" aria-hidden="true" />
        {error && <p className="pr-err" role="alert">{error}</p>}
        <p className="pr-actions"><button type="submit" className="c-btn" disabled={sending}>{sending ? t.sending : t.send}</button></p>
        <p className="pr-hint">{t.privacy} <Link to="/privacy">{lang === "he" ? "מדיניות פרטיות" : "Privacy policy"}</Link></p>
      </fieldset>
    </form>
  );
}
