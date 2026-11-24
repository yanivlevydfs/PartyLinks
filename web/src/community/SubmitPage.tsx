// /submit: a signed-in user posts a public event (partylinks.community, docs/community-events.md).
// GET /api/community/config draws the form; pictures go one by one to POST /api/community/uploads; the event to
// POST /api/community/events with the Turnstile token. Every event waits for an admin's approval (auto_publish off).
// Below the form: the account's own events and their status (GET /api/community/events), each one deletable.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { SignInButton } from "../accounts/AccountSlot";
import { send, useMe, useSite } from "../accounts/useMe";
import { useCategories } from "../api";
import { GROUP_ICON } from "../Filters";
import { genreLabel } from "../i18n";
import { Loader, useTitle } from "../ui";
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
type Text = ReturnType<typeof useSubmitText>["s"];

const TURNSTILE_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
const NEW_TICKET: Ticket = { name: "", url: "", price: "", is_free: false, sold_out: false };
const TYPE_ICON: Record<string, string> = {
  "מסיבה": "🎉", "מועדון": "🪩", "הופעה": "🎤", "פסטיבל": "🎪", "סטנדאפ": "😂", "הצגה": "🎭", "הרצאה": "🎓", "סדנה": "🛠️",
  "ילדים": "🧸", "אחר": "✨",
};

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
        sitekey: siteKey, language: lang, theme: "dark", action: "submit-event", size: "flexible",
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

function Head({ s }: { s: Text }) {
  return (
    <header className="cm-head">
      <span className="cm-badge">＋</span>
      <h1>{s.title}</h1>
      <p>{s.lead}</p>
      <ul className="cm-perks">
        <li>✓ {s.perkFree}</li>
        <li>✓ {s.perkReview}</li>
        <li>✓ {s.perkReach}</li>
      </ul>
    </header>
  );
}

export function SubmitPage() {
  const { s } = useSubmitText();
  useTitle(s.metaTitle);
  const site = useSite();
  const me = useMe();
  const cfg = useQuery({ queryKey: ["community-config"], queryFn: () => fetch("/api/community/config").then((r) => r.json() as Promise<FormConfig>) });
  if (!site || (site.accounts && me.isPending) || cfg.isPending) return <Loader />;
  const closed = (text: string) => (
    <main className="wrap cm"><Head s={s} /><p className="cm-note">{text} <Link to="/contact">→</Link></p></main>
  );
  if (!site.accounts) return closed(s.accountsOff);
  if (!cfg.data?.enabled) return closed(s.closed);
  if (cfg.data.captcha_required && !cfg.data.turnstile_site_key) return closed(s.notReady);
  if (!me.data)
    return (
      <main className="wrap cm">
        <Head s={s} />
        <section className="cm-card cm-signin">
          <h2>{s.signInTitle}</h2>
          <p>{s.signInText}</p>
          <SignInButton next={location.pathname + location.search} />
        </section>
      </main>
    );
  return <SubmitForm cfg={cfg.data} />;
}

function messageOf(detail: unknown, s: Text): string[] {
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

/** One numbered section of the form. */
function Step({ n, title, hint, children }: { n: number; title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="cm-card cm-step" aria-labelledby={`cm-step-${n}`}>
      <h2 id={`cm-step-${n}`}><span className="cm-n">{n}</span>{title}</h2>
      {hint && <p className="cm-hint">{hint}</p>}
      {children}
    </section>
  );
}

/** A labelled field: the label above, an optional note below, a star when it is required. */
function Field({ label, need, note, wide, children }: { label: string; need?: boolean; note?: string; wide?: boolean; children: ReactNode }) {
  return (
    <label className={`cm-field${wide ? " wide" : ""}`}>
      <span className="cm-label">{label}{need && <b className="cm-need" aria-hidden="true"> *</b>}</span>
      {children}
      {note && <small className="cm-note-s">{note}</small>}
    </label>
  );
}

/** Genres by family: the family tabs, the open family's genres; picks shown on top. Only real genre keys are sent. */
function Genres({ valid, value, onChange, most }: { valid: string[]; value: string[]; onChange: (v: string[]) => void; most: number }) {
  const { s, lang } = useSubmitText();
  const cats = useCategories().data;
  const ok = useMemo(() => new Set(valid), [valid]);
  const name = useCallback((g: string) => genreLabel(lang, g), [lang]);
  const families = useMemo(() => {
    const subs = cats?.genre_families ?? {};
    return (cats?.genre_groups ?? []).map((g) => {
      const all = (g.members ?? []).flatMap((m) => [m, ...(subs[m] ?? [])]);
      return { key: g.value, genres: [...new Set(all)].filter((x) => ok.has(x)).sort((a, b) => name(a).localeCompare(name(b), lang)) };
    }).filter((f) => f.genres.length);
  }, [cats, ok, name, lang]);
  const [open, setOpen] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const fam = families.find((f) => f.key === open) ?? families[0];
  const needle = q.trim().toLowerCase();
  const shown = needle ? valid.filter((g) => name(g).toLowerCase().includes(needle) || g.includes(needle)).slice(0, 40) : fam?.genres ?? [];
  const toggle = (g: string) => onChange(value.includes(g) ? value.filter((x) => x !== g) : value.length >= most ? value : [...value, g]);
  return (
    <div className="cm-genres">
      {value.length > 0 && (
        <div className="cm-chips cm-picked">
          {value.map((g) => <button type="button" key={g} className="cm-chip on" onClick={() => toggle(g)} aria-label={`${s.remove}: ${name(g)}`}>{name(g)} ✕</button>)}
        </div>
      )}
      <input className="cm-input cm-search" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={s.genreSearch} aria-label={s.genreSearch} />
      {!needle && (
        <div className="cm-tabs" role="tablist">
          {families.map((f) => (
            <button type="button" role="tab" key={f.key} aria-selected={f.key === fam?.key} className={`cm-tab${f.key === fam?.key ? " on" : ""}`}
              onClick={() => setOpen(f.key)}>
              <span aria-hidden="true">{GROUP_ICON[f.key] ?? "•"}</span> {name(f.key)}
            </button>
          ))}
        </div>
      )}
      <div className="cm-chips">
        {shown.map((g) => (
          <button type="button" key={g} className={`cm-chip${value.includes(g) ? " on" : ""}`} aria-pressed={value.includes(g)}
            disabled={!value.includes(g) && value.length >= most} onClick={() => toggle(g)}>{name(g)}</button>
        ))}
      </div>
    </div>
  );
}

function SubmitForm({ cfg }: { cfg: FormConfig }) {
  const { s, lang } = useSubmitText();
  const qc = useQueryClient();
  const L = cfg.limits;
  const [types, setTypes] = useState<string[]>([]);
  const [genres, setGenres] = useState<string[]>([]);
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
  const [descLen, setDescLen] = useState(0);
  const errorBox = useRef<HTMLDivElement>(null);
  useEffect(() => { if (errors.length) errorBox.current?.scrollIntoView({ behavior: "smooth", block: "center" }); }, [errors]);

  const toggleType = (v: string) => setTypes((now) => (now.includes(v) ? now.filter((x) => x !== v) : now.length >= L.max_event_types ? now : [...now, v]));
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
      tickets: free ? [] : real.map((t) => ({
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
      <main className="wrap cm">
        <section className="cm-card cm-done">
          <span className="cm-done-icon" aria-hidden="true">{done.status === "published" ? "🎉" : "⏳"}</span>
          <h1>{done.status === "published" ? s.doneLive : s.donePending}</h1>
          <p>{done.status === "published" ? s.doneLiveText : s.donePendingText}</p>
          <p className="cm-actions">
            {done.event_path && <Link className="c-btn" to={done.event_path}>{s.view}</Link>}
            <button type="button" className="c-btn ghost" onClick={() => {
              setDone(null); setTypes([]); setGenres([]); setTickets([{ ...NEW_TICKET }]); setFree(false); setPictures([]);
              setSponsored(false); setNonce((n) => n + 1);
            }}>{s.another}</button>
          </p>
        </section>
        <MyEvents />
      </main>
    );

  const today = new Date().toISOString().slice(0, 10);
  return (
    <main className="wrap cm">
      <Head s={s} />
      <form onSubmit={submit} className="cm-form">
        <Step n={1} title={s.stepWhat}>
          <div className="cm-grid">
            <Field label={s.name} need wide>
              <input className="cm-input" name="title" required minLength={3} maxLength={L.title_max} dir="auto" placeholder={s.namePh} />
            </Field>
            <Field label={s.description} need={L.description_min > 0} wide note={`${descLen} / ${L.description_max} · ${s.descriptionHint(L.description_min)}`}>
              <textarea className="cm-input" name="description" rows={6} minLength={L.description_min} maxLength={L.description_max}
                required={L.description_min > 0} dir="auto" placeholder={s.descriptionPh} onChange={(e) => setDescLen(e.target.value.length)} />
            </Field>
            <Field label={s.organizer}>
              <input className="cm-input" name="organizer" maxLength={160} dir="auto" autoComplete="organization" />
            </Field>
            <Field label={s.minAge}>
              <input className="cm-input" name="min_age" type="number" min={0} max={99} inputMode="numeric" placeholder="18" />
            </Field>
            <Field label={s.artists} note={s.artistsNote} wide>
              <input className="cm-input" name="artists" maxLength={2000} dir="auto" />
            </Field>
          </div>
          <div className="cm-sub">
            <span className="cm-label">{s.types}<small className="cm-count">{types.length}/{L.max_event_types}</small></span>
            <div className="cm-types">
              {cfg.event_types.map((t) => (
                <button type="button" key={t} className={`cm-type${types.includes(t) ? " on" : ""}`} aria-pressed={types.includes(t)}
                  disabled={!types.includes(t) && types.length >= L.max_event_types} onClick={() => toggleType(t)}>
                  <span aria-hidden="true">{TYPE_ICON[t] ?? "•"}</span>{t}
                </button>
              ))}
            </div>
          </div>
          <div className="cm-sub">
            <span className="cm-label">{s.genres}<small className="cm-count">{genres.length}/{L.max_genres}</small></span>
            <small className="cm-note-s">{s.genresHint}</small>
            <Genres valid={cfg.genres} value={genres} onChange={setGenres} most={L.max_genres} />
          </div>
        </Step>

        <Step n={2} title={s.stepWhen} hint={s.whenHint}>
          <div className="cm-grid cm-when">
            <Field label={s.startDate} need><input className="cm-input" name="start_date" type="date" required min={today} /></Field>
            <Field label={s.startTime}><input className="cm-input" name="start_time" type="time" /></Field>
            <Field label={s.endDate}><input className="cm-input" name="end_date" type="date" min={today} /></Field>
            <Field label={s.endTime}><input className="cm-input" name="end_time" type="time" /></Field>
            <Field label={s.doors}><input className="cm-input" name="doors_open" type="time" /></Field>
          </div>
        </Step>

        <Step n={3} title={s.stepWhere}>
          <div className="cm-grid">
            <Field label={s.venue} need><input className="cm-input" name="venue_name" required minLength={2} maxLength={120} dir="auto" placeholder={s.venuePh} /></Field>
            <Field label={s.city} need><input className="cm-input" name="city" required minLength={2} maxLength={60} dir="auto" autoComplete="address-level2" placeholder={s.cityPh} /></Field>
            <Field label={s.address} note={s.addressNote} wide><input className="cm-input" name="address" maxLength={160} dir="auto" autoComplete="street-address" /></Field>
          </div>
        </Step>

        <Step n={4} title={s.stepEntry} hint={s.entryHint}>
          <div className="cm-seg" role="radiogroup" aria-label={s.stepEntry}>
            <button type="button" role="radio" aria-checked={!free} className={!free ? "on" : ""} onClick={() => setFree(false)}>🎟️ {s.withTickets}</button>
            <button type="button" role="radio" aria-checked={free} className={free ? "on" : ""} onClick={() => setFree(true)}>🆓 {s.freeEntry}</button>
          </div>
          {!free && (
            <>
              {tickets.map((t, i) => (
                <div className="cm-ticket" key={i}>
                  <div className="cm-ticket-top">
                    <b>{s.ticket} {tickets.length > 1 ? i + 1 : ""}</b>
                    {tickets.length > 1 && <button type="button" className="cm-link" onClick={() => setTickets((now) => now.filter((_, k) => k !== i))}>{s.remove}</button>}
                  </div>
                  <div className="cm-grid">
                    <Field label={s.ticketUrl} need={i === 0} wide>
                      <input className="cm-input" value={t.url} onChange={(e) => setTicket(i, { url: e.target.value })} type="url" maxLength={1000}
                        placeholder="https://" dir="ltr" required={i === 0} inputMode="url" />
                    </Field>
                    <Field label={s.ticketName}>
                      <input className="cm-input" value={t.name} onChange={(e) => setTicket(i, { name: e.target.value })} maxLength={80} placeholder={s.ticketNamePh} dir="auto" />
                    </Field>
                    <Field label={s.ticketPrice}>
                      <input className="cm-input" value={t.is_free ? "0" : t.price} onChange={(e) => setTicket(i, { price: e.target.value })} type="number"
                        min={0} step="0.01" inputMode="decimal" disabled={t.is_free} placeholder="₪" />
                    </Field>
                  </div>
                  <div className="cm-flags">
                    <label className="cm-check"><input type="checkbox" checked={t.is_free} onChange={(e) => setTicket(i, { is_free: e.target.checked })} /> {s.ticketFree}</label>
                    <label className="cm-check"><input type="checkbox" checked={t.sold_out} onChange={(e) => setTicket(i, { sold_out: e.target.checked })} /> {s.ticketSoldOut}</label>
                  </div>
                </div>
              ))}
              {tickets.length < L.max_tickets && (
                <button type="button" className="cm-add" onClick={() => setTickets((now) => [...now, { ...NEW_TICKET }])}>{s.addTicket}</button>
              )}
            </>
          )}
        </Step>

        <Step n={5} title={s.stepPictures} hint={s.picturesHint(L.max_images, Math.round(L.upload_max_bytes / 1e6), L.upload_min_side)}>
          <div className="cm-pics">
            {pictures.map((p, i) => (
              <figure key={p.id} className="cm-pic">
                <img src={p.url} alt="" />
                {i === 0 && <figcaption>{s.cover}</figcaption>}
                <button type="button" className="cm-x" aria-label={s.remove} onClick={() => setPictures((now) => now.filter((x) => x.id !== p.id))}>✕</button>
              </figure>
            ))}
            {pictures.length < L.max_images && (
              <label className={`cm-drop${uploading ? " busy" : ""}`}>
                <span className="cm-drop-icon" aria-hidden="true">{uploading ? "⏳" : "📷"}</span>
                <span>{uploading ? s.uploading : s.upload}</span>
                <input type="file" accept="image/jpeg,image/png,image/webp,image/gif,image/avif,image/heic,image/heif" multiple disabled={uploading}
                  onChange={(e) => { upload(e.target.files); e.target.value = ""; }} />
              </label>
            )}
          </div>
        </Step>

        <Step n={6} title={s.stepMore}>
          <label className="cm-toggle">
            <input type="checkbox" checked={sponsored} onChange={(e) => setSponsored(e.target.checked)} />
            <span><b>{s.sponsored}</b><small>{s.sponsoredNote}</small></span>
          </label>
          {sponsored && (
            <div className="cm-grid">
              <Field label={s.phone} need><input className="cm-input" name="sponsor_phone" type="tel" required minLength={9} maxLength={40} inputMode="tel" dir="ltr" placeholder="05X-XXXXXXX" autoComplete="tel" /></Field>
              <Field label={s.sponsorNote}><input className="cm-input" name="sponsor_note" maxLength={L.sponsor_note_max} dir="auto" /></Field>
            </div>
          )}
          <label className="cm-toggle cm-promise">
            <input type="checkbox" name="public_event" required />
            <span><b>{s.publicEvent}</b><small>{s.publicNote}</small></span>
          </label>
          {/* Hidden from people; a bot that fills it is thanked and nothing is saved. */}
          <input className="cm-hp" name="website" tabIndex={-1} autoComplete="off" aria-hidden="true" />
          {cfg.captcha_required && <Captcha siteKey={cfg.turnstile_site_key} lang={lang} nonce={nonce} onToken={setToken} />}
        </Step>

        {errors.length > 0 && (
          <div className="cm-err" role="alert" ref={errorBox}>{errors.map((e, i) => <p key={i}>{e}</p>)}</div>
        )}
        <div className="cm-submit">
          <button type="submit" className="c-btn cm-send" disabled={sending || uploading}>{sending ? s.sending : s.send}</button>
          <small>{s.reviewNote} · {s.privacy} <Link to="/privacy">{s.privacyLink}</Link></small>
        </div>
      </form>
      <MyEvents />
    </main>
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
    <section className="cm-card cm-mine">
      <h2>{s.mine}</h2>
      <ul>
        {mine.data.map((e) => (
          <li key={e.id}>
            <div>
              <b dir="auto">{e.event_path ? <Link to={e.event_path}>{e.title}</Link> : e.title}</b>
              <small>{new Date(e.start_date).toLocaleDateString(s.locale)}</small>
              {e.reject_reason && <small className="cm-reason">{s.reason}{e.reject_reason}</small>}
            </div>
            <span className={`cm-status ${e.status}`}>{s.status[e.status] ?? e.status}</span>
            {e.status !== "removed" && <button type="button" className="cm-link" onClick={() => remove(e.id)}>{s.del}</button>}
          </li>
        ))}
      </ul>
    </section>
  );
}
