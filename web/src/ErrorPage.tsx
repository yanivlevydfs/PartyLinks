// What a visitor sees when a page throws (the router's errorElement), instead of React Router's developer screen.
// Standalone on purpose: no store, queries or Layout, which may be what broke; the language from <html lang>.
import { useRouteError } from "react-router-dom";
import "./error-page.css";

const TEXT = {
  he: { title: "יש תקלה במערכת", body: "משהו השתבש בטעינת העמוד. אנחנו כבר על זה.", retry: "לנסות שוב", home: "לדף הבית" },
  en: { title: "Something went wrong", body: "This page failed to load. We're on it.", retry: "Try again", home: "Home page" },
};

export default function ErrorPage() {
  console.error(useRouteError()); // kept for the browser console; never shown to visitors
  const t = TEXT[document.documentElement.lang === "en" ? "en" : "he"];
  return (
    <main className="wrap error-page" role="alert">
      <h1>⚠️ {t.title}</h1>
      <p>{t.body}</p>
      <div className="error-actions">
        <button className="nf-btn" onClick={() => location.reload()}>{t.retry}</button>
        <a className="nf-btn ghost" href="/">{t.home}</a>
      </div>
    </main>
  );
}
