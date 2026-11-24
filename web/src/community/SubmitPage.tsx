// /submit: a signed-in user posts a public event (partylinks.community, docs/community-events.md).
// GET /api/community/config draws the form; pictures go one by one to POST /api/community/uploads; the event to
// POST /api/community/events with the Turnstile token. Every event waits for an admin's approval (auto_publish off).
// Below the form: the account's own events and their status (GET /api/community/events), each one deletable.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { SignInButton } from "../accounts/AccountSlot";
import { send, useMe, useSite } from "../accounts/useMe";
import { genreLabel } from "../i18n";
import { Hero, Loader, useTitle } from "../ui";
import { useSubmitText } from "./i18n";
import "./community.css";

interface FormConfig {
  enabled: boolean;
  captcha_required: boolean;
  turnstile_site_key: string;
  event_types: string[];
  genres: string[];
  limits: Record<string, number>;
}
interface Mine {
  id: number;
  status: string;
  title: string;
  start_date: string;
  reject_reason: string | null;
  event_path: string | null;
}
interface Ticket { name: string; url: string; price: string; is_free: boolean; sold_out: boolean }
interface Picture { id: string; url: string }
interface Turnstile {
  render: (el: HTMLElement, opts: Record<string, unknown>) => string;
  reset: (id?: string) => void;
  remove: (id?: string) => void;
}
declare global { interface Window { turnstile?: Turnstile } }

const TURNSTILE_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
const NEW_TICKET: Ticket = { name: "", url: "", price: "", is_free: false, sold_out: false };

function loadTurnstile(): Promise<Turnstile> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  return new Promise((resolve, reject) => {
    let script = document.querySelector<HTMLScriptElement>(`script[src="${TURNSTILE_SRC}"]`);
    if (!script) {
      script = document.createElement("script");
      script.src = TURNSTILE_SRC;
      script.async = true;
      document.head.appendChild(script);
    }
    script.addEventListener("load", () => (window.turnstile ? resolve(window.turnstile) : reject(new Error("turnstile"))));
    script.addEventListener("error", () => reject(new Error("turnstile")));
  });
}

/** The Turnstile widget: hands its one-time token up; ``nonce`` changing resets it (a token is good once). */
function Captcha({ siteKey, lang, nonce, onToken }: { siteKey: string; lang: string; nonce: number; onToken: (t: string) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const widget = useRef<string | null>(null);
  useEffect(() => {
    let gone = false;
    loadTurnstile().then((ts) => {
      if (gone || !box.current || widget.current) return;
      widget.current = ts.render(box.current, {
        sitekey: siteKey, language: lang, theme: "auto", action: "submit-event",
        callback: (token: string) => onToken(token),
        "expired-callback": () => onToken(""),
        "error-callback": () => onToken(""),
      });
    }).catch(() => onToken(""));
    return () => {
      gone = true;
      if (widget.current) window.turnstile?.remove(widget.current);
      widget.current = null;
    };
  }, [siteKey, lang, onToken]);
  useEffect(() => {
    if (nonce && widget.current) {
      window.turnstile?.reset(widget.current);
      onToken("");
    }
  }, [nonce, onToken]);
  return <div ref={box} className="cm-captcha" />;
}

export function SubmitPage() {
  const { s } = useSubmitText();
  useTitle(s.metaTitle);
  const site = useSite();
  const me = useMe();
  const cfg = useQuery({ queryKey: ["community-config"], queryFn: () => fetch("/api/community/config").then((r) => r.json() as Promise<FormConfig>) });
  const head = <Hero title={s.title}><p className="hero-pitch">{s.lead}</p></Hero>;
  if (!site || (site.accounts && me.isPending) || cfg.isPending) return <Loader />;
  const closed = (text: string) => <>{head}<main className="wrap cm"><p className="cm-err">{text} <Link to="/contact">→</Link></p></main></>;
  if (!site.accounts) return closed(s.accountsOff);
  if (!cfg.data?.enabled) return closed(s.closed);
  if (cfg.data.captcha_required && !cfg.data.turnstile_site_key) return closed(s.notReady);
  if (!me.data)
    return (
      <>
        {head}
        <main className="wrap cm">
          <section className="cm-signin">
            <h2>{s.signInTitle}</h2>
            <p>{s.signInText}</p>
            <SignInButton next={location.pathname + location.search} />
          </section>
        </main>
      </>
    );
  return <><SubmitForm cfg={cfg.data} /><MyEvents /></>;
}

function messageOf(detail: unknown, s: ReturnType<typeof useSubmitText>["s"]): string[] {
  const items = Array.isArray(detail) ? detail : detail ? [detail] : [];
  return items.map((d) => {
    if (typeof d === "string") {
      const [field, ...rest] = d.split(":");
      const key = field.replace(/\[\d+\].*$/, "");
      return s.fields[key] ? `${s.fields[key]}:${rest.join(":")}` : d;
    }
    const loc = (d?.loc ?? []).filter((x: unknown) => x !== "body");
    const key = String(loc[0] ?? "");
    return `${s.fields[key] ?? key}: ${String(d?.msg ?? "").replace(/^Value error, /, "")}`;
  });
}

function SubmitForm({ cfg }: { cfg: FormConfig }) {
  const { s, lang } = useSubmitText();
  const qc = useQueryClient();
  const L = cfg.limits;
  const [types, setTypes] = useState<string[]>([]);
  const [genres, setGenres] = useState<string[]>([]);
  const [genreQ, setGenreQ] = useState("");
  const [tickets, setTickets] = useState<Ticket[]>([{ ...NEW_TICKET }]);
  const [free, setFree] = useState(false);
  const [pictures, setPictures] = useState<Picture[]>([]);
  const [uploading, setUploading] = useState(false);
  const [sponsored, setSponsored] = useState(false);
  const [token, setToken] = useState("");
  const [nonce, setNonce] = useState(0);
  const [errors, setErrors] = useState<string[]>([]);
  const [sending, setSending] = useState(false);
  const [done, setDone] = useState<Mine | null>(null);
  const formRef = useRef<HTMLFormElement>(null);

  const shownGenres = useMemo(() => {
    const q = genreQ.trim().toLowerCase();
    return cfg.genres.filter((g) => !q || genreLabel(lang, g).toLowerCase().includes(q) || g.includes(q));
  }, [cfg.genres, genreQ, lang]);
  const toggle = (list: string[], set: (v: string[]) => void, v: string, most: number) =>
    set(list.includes(v) ? list.filter((x) => x !== v) : list.length >= most ? list : [...list, v]);
  const setTicket = (i: number, patch: Partial<Ticket>) => setTickets((now) => now.map((t, k) => (k === i ? { ...t, ...patch } : t)));

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setUploading(true);
    setErrors([]);
    for (const file of Array.from(files).slice(0, L.max_images - pictures.length)) {
      if (file.size > L.upload_max_bytes) {
        setErrors((e) => [...e, `${s.badPicture}${file.name} (> ${Math.round(L.upload_max_bytes / 1e6)}MB)`]);
        continue;
      }
      const body = new FormData();
      body.append("file", file);
      try {
        const res = await fetch("/api/community/uploads", { method: "POST", credentials: "same-origin", headers: { "X-Requested-With": "spotx" }, body });
        const json = await res.json().catch(() => null);
        if (res.ok) setPictures((now) => [...now, { id: json.id, url: json.url }]);
        else setErrors((e) => [...e, `${s.badPicture}${file.name}: ${messageOf(json?.detail, s).join(", ") || res.status}`]);
      } catch {
        setErrors((e) => [...e, `${s.badPicture}${file.name}`]);
      }
    }
    setUploading(false);
  }

  async function submit(ev: FormEvent<HTMLFormElement>) {
    ev.preventDefault();
    const f = new FormData(ev.currentTarget);
    const str = (k: string) => String(f.get(k) ?? "").trim();
    const real = tickets.filter((t) => t.url.trim());
    const problems: string[] = [];
    if (!free && !real.length) problems.push(s.needTicket);
    if (!f.get("public_event")) problems.push(s.needPublic);
    if (cfg.captcha_required && !token) problems.push(s.needCaptcha);
    if (problems.length) return setErrors(problems);
    const body = {
      title: str("title"), description: str("description"), organizer: str("organizer"),
      artists: str("artists").split(/[,،\n]/).map((a) => a.trim()).filter(Boolean),
      min_age: str("min_age") ? Number(str("min_age")) : null,
      start_date: str("start_date"), start_time: str("start_time") || null,
      end_date: str("end_date") || null, end_time: str("end_time") || null, doors_open: str("doors_open") || null,
      venue_name: str("venue_name"), address: str("address"), city: str("city"),
      event_types: types, genres,
      tickets: real.map((t) => ({
        name: t.name.trim() || (lang === "he" ? "כרטיס" : "Ticket"), url: t.url.trim(),
        price: t.is_free ? 0 : t.price.trim() ? Number(t.price) : null, is_free: t.is_free, sold_out: t.sold_out,
      })),
      free_entry: free, pictures: pictures.map((p) => p.id),
      wants_sponsored: sponsored, sponsor_phone: sponsored ? str("sponsor_phone") : "", sponsor_note: sponsored ? str("sponsor_note") : "",
      captcha_token: token, public_event: true, website: str("website"),
    };
    setSending(true);
    setErrors([]);
    try {
      const res = await fetch("/api/community/events", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Requested-With": "spotx" },
        body: JSON.stringify(body),
      });
      const json = await res.json().catch(() => null);
      if (res.ok) {
        setDone(json);
        qc.invalidateQueries({ queryKey: ["community-mine"] });
        window.scrollTo({ top: 0 });
        return;
      }
      setNonce((n) => n + 1); // the token is spent: a new one for the next try
      setErrors(
        res.status === 401 ? [s.signInAgain]
        : res.status === 429 ? [String(json?.detail ?? "").includes("a day") ? s.daily : s.tooMany]
        : res.status === 404 ? [s.closed]
        : res.status === 503 ? [s.notReady]
        : res.status === 400 ? [s.needCaptcha]
        : res.status === 422 ? [s.problems, ...messageOf(json?.detail, s)]
        : [s.error],
      );
    } catch {
      setErrors([s.error]);
    } finally {
      setSending(false);
    }
  }

  if (done)
    return (
      <>
        <Hero title={done.status === "published" ? s.doneLive : s.donePending}>
          <p className="hero-pitch">{done.status === "published" ? s.doneLiveText : s.donePendingText}</p>
        </Hero>
        <main className="wrap cm">
          <p className="cm-actions">
            {done.event_path && <Link className="c-btn" to={done.event_path}>{s.view}</Link>}
            <button type="button" className="c-btn ghost" onClick={() => {
              setDone(null); setTypes([]); setGenres([]); setTickets([{ ...NEW_TICKET }]); setFree(false); setPictures([]);
              setSponsored(false); setNonce((n) => n + 1);
            }}>{s.another}</button>
          </p>
        </main>
      </>
    );

  const today = new Date().toISOString().slice(0, 10);
  return (
    <>
      <Hero title={s.title}><p className="hero-pitch">{s.lead}</p></Hero>
      <main className="wrap cm">
        <form ref={formRef} onSubmit={submit}>
          <fieldset>
            <legend>{s.stepWhat}</legend>
            <label className="cm-wide">{s.name}<input className="cm-input" name="title" required minLength={3} maxLength={L.title_max} dir="auto" /></label>
            <label className="cm-wide">{s.description}
              <textarea className="cm-input" name="description" rows={6} minLength={L.description_min} maxLength={L.description_max} required={L.description_min > 0} dir="auto" />
              <small className="cm-hint">{s.descriptionHint(L.description_min, L.description_max)}</small>
            </label>
            <div className="cm-fields">
              <label>{s.organizer}<input className="cm-input" name="organizer" maxLength={160} dir="auto" autoComplete="organization" /></label>
              <label>{s.minAge}<input className="cm-input" name="min_age" type="number" min={0} max={99} inputMode="numeric" /></label>
            </div>
            <label className="cm-wide">{s.artists}<input className="cm-input" name="artists" maxLength={2000} dir="auto" /></label>
            <p className="cm-label">{s.types} <small className="cm-hint">{s.typesHint(L.max_event_types)}</small></p>
            <div className="cm-chips">
              {cfg.event_types.map((t) => (
                <button type="button" key={t} className={`cm-chip${types.includes(t) ? " on" : ""}`} aria-pressed={types.includes(t)}
                  onClick={() => toggle(types, setTypes, t, L.max_event_types)}>{t}</button>
              ))}
            </div>
            <p className="cm-label">{s.genres} <small className="cm-hint">{s.genresHint(L.max_genres)}</small></p>
            {genres.length > 0 && (
              <div className="cm-chips">
                {genres.map((g) => <button type="button" key={g} className="cm-chip on" aria-pressed onClick={() => toggle(genres, setGenres, g, L.max_genres)}>✓ {genreLabel(lang, g)}</button>)}
              </div>
            )}
            <input className="cm-input" type="search" value={genreQ} onChange={(e) => setGenreQ(e.target.value)} placeholder={s.genreSearch} aria-label={s.genreSearch} />
            <div className="cm-chips cm-scroll">
              {shownGenres.filter((g) => !genres.includes(g)).map((g) => (
                <button type="button" key={g} className="cm-chip" aria-pressed={false} disabled={genres.length >= L.max_genres}
                  onClick={() => toggle(genres, setGenres, g, L.max_genres)}>{genreLabel(lang, g)}</button>
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend>{s.stepWhen}</legend>
            <div className="cm-fields">
              <label>{s.startDate}<input className="cm-input" name="start_date" type="date" required min={today} /></label>
              <label>{s.startTime}<input className="cm-input" name="start_time" type="time" /></label>
              <label>{s.endDate}<input className="cm-input" name="end_date" type="date" min={today} /></label>
              <label>{s.endTime}<input className="cm-input" name="end_time" type="time" /></label>
              <label>{s.doors}<input className="cm-input" name="doors_open" type="time" /></label>
            </div>
            <p className="cm-hint">{s.whenHint}</p>
          </fieldset>

          <fieldset>
            <legend>{s.stepWhere}</legend>
            <div className="cm-fields">
              <label>{s.venue}<input className="cm-input" name="venue_name" required minLength={2} maxLength={120} dir="auto" /></label>
              <label>{s.city}<input className="cm-input" name="city" required minLength={2} maxLength={60} dir="auto" autoComplete="address-level2" /></label>
            </div>
            <label className="cm-wide">{s.address}<input className="cm-input" name="address" maxLength={160} dir="auto" autoComplete="street-address" /></label>
          </fieldset>

          <fieldset>
            <legend>{s.stepEntry}</legend>
            <p className="cm-hint">{s.entryHint}</p>
            <label className="cm-check"><input type="checkbox" checked={free} onChange={(e) => setFree(e.target.checked)} /> {s.freeEntry}</label>
            {tickets.map((t, i) => (
              <div className="cm-ticket" key={i}>
                <label>{s.ticketName}<input className="cm-input" value={t.name} onChange={(e) => setTicket(i, { name: e.target.value })} maxLength={80} placeholder={s.ticketNamePh} dir="auto" /></label>
                <label className="cm-url">{s.ticketUrl}<input className="cm-input" value={t.url} onChange={(e) => setTicket(i, { url: e.target.value })} type="url" maxLength={1000} placeholder="https://" dir="ltr" required={!free && i === 0} /></label>
                <label>{s.ticketPrice}<input className="cm-input" value={t.price} onChange={(e) => setTicket(i, { price: e.target.value })} type="number" min={0} step="0.01" inputMode="decimal" disabled={t.is_free} /></label>
                <div className="cm-ticket-flags">
                  <label className="cm-check"><input type="checkbox" checked={t.is_free} onChange={(e) => setTicket(i, { is_free: e.target.checked })} /> {s.ticketFree}</label>
                  <label className="cm-check"><input type="checkbox" checked={t.sold_out} onChange={(e) => setTicket(i, { sold_out: e.target.checked })} /> {s.ticketSoldOut}</label>
                  {tickets.length > 1 && <button type="button" className="link-btn" onClick={() => setTickets((now) => now.filter((_, k) => k !== i))}>{s.remove}</button>}
                </div>
              </div>
            ))}
            {tickets.length < L.max_tickets && <button type="button" className="c-btn ghost" onClick={() => setTickets((now) => [...now, { ...NEW_TICKET }])}>{s.addTicket}</button>}
          </fieldset>

          <fieldset>
            <legend>{s.stepPictures}</legend>
            <p className="cm-hint">{s.picturesHint(L.max_images, Math.round(L.upload_max_bytes / 1e6), L.upload_min_side)}</p>
            {pictures.length > 0 && (
              <ul className="cm-pics">
                {pictures.map((p, i) => (
                  <li key={p.id}>
                    <img src={p.url} alt="" />
                    <button type="button" className="cm-x" aria-label={s.remove} onClick={() => setPictures((now) => now.filter((x) => x.id !== p.id))}>✕</button>
                    {i === 0 && <small>★</small>}
                  </li>
                ))}
              </ul>
            )}
            {pictures.length < L.max_images && (
              <label className="c-btn ghost cm-upload">
                {uploading ? s.uploading : s.upload}
                <input type="file" accept="image/jpeg,image/png,image/webp,image/gif,image/avif,image/heic,image/heif" multiple disabled={uploading}
                  onChange={(e) => { upload(e.target.files); e.target.value = ""; }} />
              </label>
            )}
          </fieldset>

          <fieldset>
            <legend>{s.stepMore}</legend>
            <label className="cm-check"><input type="checkbox" checked={sponsored} onChange={(e) => setSponsored(e.target.checked)} /> {s.sponsored}</label>
            {sponsored && (
              <div className="cm-fields">
                <label>{s.phone}<input className="cm-input" name="sponsor_phone" type="tel" required minLength={9} maxLength={40} inputMode="tel" dir="ltr" placeholder="05X-XXXXXXX" autoComplete="tel" /></label>
                <label>{s.sponsorNote}<input className="cm-input" name="sponsor_note" maxLength={L.sponsor_note_max} dir="auto" /></label>
              </div>
            )}
            <label className="cm-check"><input type="checkbox" name="public_event" required /> {s.publicEvent}</label>
            {/* Hidden from people; a bot that fills it is thanked and nothing is saved. */}
            <input className="cm-hp" name="website" tabIndex={-1} autoComplete="off" aria-hidden="true" />
            {cfg.captcha_required && (
              <>
                <p className="cm-label">{s.captcha}</p>
                <Captcha siteKey={cfg.turnstile_site_key} lang={lang} nonce={nonce} onToken={setToken} />
              </>
            )}
          </fieldset>

          {errors.length > 0 && (
            <div className="cm-err" role="alert">{errors.map((e, i) => <p key={i}>{e}</p>)}</div>
          )}
          <p className="cm-actions">
            <button type="submit" className="c-btn" disabled={sending || uploading}>{sending ? s.sending : s.send}</button>
          </p>
          <p className="cm-hint">{s.privacy} <Link to="/privacy">{s.privacyLink}</Link></p>
        </form>
      </main>
    </>
  );
}

function MyEvents() {
  const { s } = useSubmitText();
  const qc = useQueryClient();
  const mine = useQuery({
    queryKey: ["community-mine"],
    queryFn: async () => {
      const res = await fetch("/api/community/events", { credentials: "same-origin" });
      return res.ok ? ((await res.json()) as Mine[]) : [];
    },
  });
  if (!mine.data?.length) return null;
  const remove = async (id: number) => {
    if (!window.confirm(s.confirmDelete)) return;
    await send(`/api/community/events/${id}`, "DELETE").catch(() => null);
    qc.invalidateQueries({ queryKey: ["community-mine"] });
  };
  return (
    <section className="wrap cm cm-mine">
      <h2>{s.mine}</h2>
      <ul>
        {mine.data.map((e) => (
          <li key={e.id}>
            <div>
              <b dir="auto">{e.event_path ? <Link to={e.event_path}>{e.title}</Link> : e.title}</b>
              <small>{new Date(e.start_date).toLocaleDateString(s.locale)} · <span className={`cm-status ${e.status}`}>{s.status[e.status] ?? e.status}</span></small>
              {e.reject_reason && <small className="cm-reason">{s.reason}{e.reject_reason}</small>}
            </div>
            {e.status !== "removed" && <button type="button" className="link-btn" onClick={() => remove(e.id)}>{s.del}</button>}
          </li>
        ))}
      </ul>
    </section>
  );
}
