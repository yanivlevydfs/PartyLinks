// /promote: an event owner picks their upcoming events (the site's own search, which lists upcoming events
// only), says what to promote and leaves their details. POST /api/promote-requests (partylinks.promote).
// ?event=<id> starts with that event picked (a link from an event page or an email).
// Signed-in users only (the owner's call, 2026-11-13): signed out, the page offers sign-in and comes back here.
import { useQuery } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { SignInButton } from "../accounts/AccountSlot";
import { useMe, useSite } from "../accounts/useMe";
import { getJSON } from "../api";
import { useWeb } from "../site_params/useWeb";
import type { EventDetail, EventSummary } from "../types";
import { Hero, Img, Loader, sized, TZ, useTitle } from "../ui";
import { usePromoteText } from "./i18n";
import "./promote.css";

type Picked = Pick<EventSummary, "id" | "title" | "start_at" | "venue_name" | "city" | "cover_url" | "image_url">;
const PLACEMENTS = ["rail", "results", "home", "unsure"] as const;
const MAX_EVENTS = 10; // promote.max_events; the server has the last word

function EventRow({ e, children }: { e: Picked; children: ReactNode }) {
  const { p } = usePromoteText();
  const when = e.start_at
    ? new Date(e.start_at).toLocaleString(p.locale, { timeZone: TZ, weekday: "short", day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" })
    : "";
  return (
    <li className="pr-event">
      <Img src={sized(e.cover_url ?? e.image_url, 640)} alt="" className="pr-thumb" />
      <div className="pr-what">
        <b dir="auto">{e.title}</b>
        <small>{when}{e.venue_name && <> · <span dir="auto">{e.venue_name}</span></>}{e.city && e.city !== e.venue_name && <>, {e.city}</>}</small>
      </div>
      {children}
    </li>
  );
}

export function PromotePage() {
  const { p } = usePromoteText();
  useTitle(p.metaTitle);
  const site = useSite();
  const me = useMe();
  if (!site || (site.accounts && me.isPending)) return <Loader />;
  if (!site.accounts)
    return (
      <>
        <Hero title={p.title}><p className="hero-pitch">{p.lead}</p></Hero>
        <main className="wrap pr"><p className="pr-err">{p.accountsOff} <Link to="/contact">→</Link></p></main>
      </>
    );
  if (!me.data)
    return (
      <>
        <Hero title={p.title}><p className="hero-pitch">{p.lead}</p></Hero>
        <main className="wrap pr">
          <section className="pr-signin">
            <h2>{p.signInTitle}</h2>
            <p>{p.signInText}</p>
            <SignInButton next={location.pathname + location.search} />
          </section>
        </main>
      </>
    );
  return <PromoteForm name={me.data.name} email={me.data.email} />;
}

function PromoteForm({ name, email }: { name: string; email: string }) {
  const { p, lang } = usePromoteText();
  const [params] = useSearchParams();
  const first = params.get("event");
  const [picked, setPicked] = useState<Picked[]>([]);
  const [q, setQ] = useState("");
  const [query, setQuery] = useState(""); // what was typed, once typing pauses: one search, not one a keystroke
  const debounce = useWeb().search_debounce_ms;
  useEffect(() => {
    const id = setTimeout(() => setQuery(q.trim()), debounce);
    return () => clearTimeout(id);
  }, [q, debounce]);
  const [placements, setPlacements] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [done, setDone] = useState(false);

  // ?event=<id>: picked once it loads (an event that has passed simply isn't added)
  useQuery({
    queryKey: ["promote-first", first],
    queryFn: async () => {
      const e = await getJSON<EventDetail>(`/events/${encodeURIComponent(first!)}`);
      setPicked((now) => (now.some((x) => x.id === e.id) ? now : [e, ...now]));
      return e;
    },
    enabled: !!first,
    retry: false,
  });
  const found = useQuery({
    queryKey: ["promote-search", query],
    // The form's own light search (title, venue, lineup of upcoming events): the site's full one is too slow here.
    queryFn: ({ signal }) => getJSON<Picked[]>(`/api/promote-requests/events?q=${encodeURIComponent(query)}`, signal),
    enabled: query.length >= 2,
    staleTime: 60_000,
  });

  const toggle = (e: Picked) =>
    setPicked((now) => (now.some((x) => x.id === e.id) ? now.filter((x) => x.id !== e.id) : now.length >= MAX_EVENTS ? now : [...now, e]));
  const togglePlacement = (key: string) =>
    setPlacements((now) => (now.includes(key) ? now.filter((k) => k !== key) : [...now, key]));

  async function submit(ev: FormEvent<HTMLFormElement>) {
    ev.preventDefault();
    if (!picked.length) return setError(p.needEvent);
    const form = new FormData(ev.currentTarget);
    const body = {
      name: form.get("name"), business: form.get("business"), email: form.get("email"), phone: form.get("phone"),
      message: form.get("message"), website: form.get("website"),
      event_ids: picked.map((e) => e.id), placements, lang,
    };
    setSending(true);
    setError("");
    try {
      const res = await fetch("/api/promote-requests", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Requested-With": "spotx" },
        body: JSON.stringify(body),
      });
      if (res.ok) return setDone(true);
      const detail = JSON.stringify((await res.json().catch(() => null))?.detail ?? "");
      setError(
        res.status === 401 ? p.signInAgain
        : res.status === 429 ? p.tooMany
        : res.status === 404 ? p.closed
        : detail.includes("phone") ? p.badPhone
        : detail.includes("email") ? p.badEmail
        : detail.includes("not upcoming") ? p.gone
        : p.error,
      );
    } catch {
      setError(p.error);
    } finally {
      setSending(false);
    }
  }

  if (done)
    return (
      <>
        <Hero title={p.thanks}><p className="hero-pitch">{p.thanksText}</p></Hero>
        <main className="wrap pr">
          <p className="pr-actions">
            <button type="button" className="c-btn" onClick={() => { setDone(false); setPicked([]); setPlacements([]); }}>{p.another}</button>
            <Link className="c-btn ghost" to="/partners">{p.back}</Link>
          </p>
        </main>
      </>
    );

  const results = found.data ?? [];
  return (
    <>
      <Hero title={p.title}><p className="hero-pitch">{p.lead}</p></Hero>
      <main className="wrap pr">
        <form onSubmit={submit} noValidate={false}>
          <fieldset>
            <legend>{p.stepEvents}</legend>
            <p className="pr-hint">{p.stepEventsHint}</p>
            {picked.length > 0 && (
              <ul className="pr-list pr-picked" aria-label={p.picked}>
                {picked.map((e) => (
                  <EventRow key={e.id} e={e}>
                    <button type="button" className="pr-x" onClick={() => toggle(e)} aria-label={`${p.remove}: ${e.title}`}>✕</button>
                  </EventRow>
                ))}
              </ul>
            )}
            {picked.length >= MAX_EVENTS && <p className="pr-hint">{p.max(MAX_EVENTS)}</p>}
            <input className="pr-input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={p.search} aria-label={p.search} enterKeyHint="search" />
            <div aria-live="polite">
              {query.length === 1 && <p className="pr-hint">{p.minChars}</p>}
              {query.length >= 2 && found.isFetching && !results.length && <p className="pr-hint">{p.searching}</p>}
              {query.length >= 2 && found.isSuccess && !results.length && <p className="pr-hint">{p.none}</p>}
              {query.length >= 2 && found.isError && <p className="pr-err">{p.error}</p>}
            </div>
            {query.length >= 2 && results.length > 0 && (
              <ul className="pr-list">
                {results.map((e) => {
                  const on = picked.some((x) => x.id === e.id);
                  return (
                    <EventRow key={e.id} e={e}>
                      <button type="button" className={`pr-add${on ? " on" : ""}`} aria-pressed={on} onClick={() => toggle(e)}
                        disabled={!on && picked.length >= MAX_EVENTS}>{on ? `✓ ${p.added}` : `+ ${p.add}`}</button>
                    </EventRow>
                  );
                })}
              </ul>
            )}
            <p className="pr-hint">{p.notListed}</p>
          </fieldset>

          <fieldset>
            <legend>{p.stepWhat}</legend>
            <p className="pr-hint">{p.stepWhatHint}</p>
            <div className="pr-opts">
              {PLACEMENTS.map((k) => (
                <label key={k} className={placements.includes(k) ? "on" : ""}>
                  <input type="checkbox" checked={placements.includes(k)} onChange={() => togglePlacement(k)} />
                  <b>{p.placements[k][0]}</b>
                  <small>{p.placements[k][1]}</small>
                </label>
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend>{p.stepDetails}</legend>
            <div className="pr-fields">
              <label>{p.name}<input className="pr-input" name="name" required minLength={2} maxLength={120} autoComplete="name" defaultValue={name} /></label>
              <label>{p.business}<input className="pr-input" name="business" maxLength={160} autoComplete="organization" /></label>
              <label>{p.email}<input className="pr-input" name="email" type="email" required maxLength={254} autoComplete="email" dir="ltr" defaultValue={email} /></label>
              <label>{p.phone}<input className="pr-input" name="phone" type="tel" required minLength={9} maxLength={40} autoComplete="tel" inputMode="tel" dir="ltr" placeholder="05X-XXXXXXX" /></label>
            </div>
            <label className="pr-wide">{p.message}
              <textarea className="pr-input" name="message" rows={5} maxLength={2000} placeholder={p.messageHint} />
            </label>
            {/* Hidden from people; a bot that fills it is thanked and nothing is saved. */}
            <input className="pr-hp" name="website" tabIndex={-1} autoComplete="off" aria-hidden="true" />
          </fieldset>

          {error && <p className="pr-err" role="alert">{error}</p>}
          <p className="pr-actions">
            <button type="submit" className="c-btn" disabled={sending}>{sending ? p.sending : p.send}</button>
          </p>
          <p className="pr-hint">{p.privacy} <Link to="/privacy">{p.privacyLink}</Link></p>
        </form>
      </main>
    </>
  );
}
