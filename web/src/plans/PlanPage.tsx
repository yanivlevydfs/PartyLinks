// /plan/:id: "who's coming with me?" for one event. Friends open it from WhatsApp, write a name and
// answer (in / maybe / not this time), no sign-up; the answers refresh while the page is open.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { EventSummary } from "../types";
import { AddToCalendar, eventPath, Img, Loader, sized, useT, useTitle, when } from "../ui";
import { forecastAt, Weather } from "../Weather";
import { usePlanText } from "./i18n";
import "./plans.css";

type Answer = "yes" | "maybe" | "no";
interface Vote { name: string; answer: Answer; mine: boolean }
interface Plan { id: string; event: EventSummary; votes: Vote[] }

// The browser's voter key (changes its own answer) and the name it last used. Storage can be off
// (private mode): then the key lives for this visit only.
const stored = (key: string, make: () => string) => {
  try {
    const v = localStorage.getItem(key) ?? make();
    localStorage.setItem(key, v);
    return v;
  } catch {
    return make();
  }
};
const voter = stored("plan-voter", () => crypto.randomUUID());
const remember = (name: string) => { try { localStorage.setItem("plan-name", name); } catch { /* off */ } };

const ANSWERS: Answer[] = ["yes", "maybe", "no"];
const wa = (text: string) => `https://wa.me/?text=${encodeURIComponent(text)}`;

export default function PlanPage() {
  const { id = "" } = useParams();
  const { t } = useT();
  const p = usePlanText();
  const client = useQueryClient();
  const key = ["plan", id];
  const { data, error, isPending } = useQuery({
    queryKey: key,
    queryFn: async () => {
      const res = await fetch(`/plans/${encodeURIComponent(id)}?me=${voter}`);
      if (!res.ok) throw new Error(String(res.status));
      return (await res.json()) as Plan;
    },
    refetchInterval: 20_000, // friends answer while it is open; 3 requests a minute, well under the rate limit
    retry: false,
  });
  const e = data?.event;
  useTitle(e ? `${p.title} ${e.title}` : p.title);
  const mine = data?.votes.find((v) => v.mine);
  const [name, setName] = useState(() => stored("plan-name", () => ""));
  const [note, setNote] = useState("");
  const [copied, setCopied] = useState(false);

  if (isPending) return <Loader />;
  if (error || !data || !e) {
    return <main className="wrap plan"><p className="msg">{p.gone}</p><Link className="f-pill" to="/">SPOTX</Link></main>;
  }

  const url = `${window.location.origin}/plan/${data.id}`;
  const whenText = when(e, t, { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
  const where = [e.venue_name, e.city].filter(Boolean).join(", ");
  const by = (a: Answer) => data.votes.filter((v) => v.answer === a);
  const coming = by("yes").map((v) => v.name);

  const answer = async (a: Answer) => {
    const clean = name.trim();
    if (!clean) return setNote(p.nameFirst);
    const res = await fetch(`/plans/${encodeURIComponent(id)}/votes`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ voter, name: clean, answer: a }),
    });
    if (res.ok) {
      remember(clean);
      client.setQueryData(key, await res.json());
      setNote(p.saved);
    } else setNote(res.status === 409 ? p.full : p.failed);
  };
  const copy = () => navigator.clipboard?.writeText(url).then(() => setCopied(true), () => {});

  return (
    <main className="wrap plan">
      <Link to={eventPath(e)} className="plan-event">
        <Img src={sized(e.cover_url ?? e.image_url, 640)} alt={e.title} className="plan-img" text={e.title} />
        <div>
          <h1 dir="auto">{p.title} <span>{e.title}</span></h1>
          <p>📅 {whenText}</p>
          {where && <p dir="auto">📍 {where}</p>}
          <ul className="facts"><Weather lat={e.latitude} lng={e.longitude} city={e.city} start={forecastAt(e)} zone={e.time_zone} full /></ul>
        </div>
      </Link>

      <section className="plan-share">
        <p>{p.intro}</p>
        <a className="plan-wa" href={wa(`${p.message(e.title, whenText, where)}\n${url}`)} target="_blank" rel="noopener noreferrer">
          {p.sendWhatsapp}
        </a>
        <div className="plan-more">
          {typeof navigator.share === "function" && (
            <button type="button" className="f-pill" onClick={() => navigator.share({ title: `${p.title} ${e.title}`, url }).catch(() => {})}>{p.shareOther}</button>
          )}
          <button type="button" className="f-pill" onClick={copy}>{copied ? p.copied : p.copy}</button>
        </div>
      </section>

      <section className="plan-vote" aria-labelledby="plan-vote-h">
        <h2 id="plan-vote-h">{mine ? p.change : p.yourName}</h2>
        <input className="search" value={name} maxLength={40} onChange={(ev) => { setName(ev.target.value); setNote(""); }}
          placeholder={p.yourName} aria-label={p.yourName} autoComplete="given-name" />
        <div className="plan-answers" role="group" aria-label={p.title}>
          {ANSWERS.map((a) => (
            <button key={a} type="button" className={`plan-a ${a}` + (mine?.answer === a ? " on" : "")} aria-pressed={mine?.answer === a} onClick={() => answer(a)}>
              {p[a]}
            </button>
          ))}
        </div>
        {note && <p className="plan-note" role="status">{note}</p>}
      </section>

      <section className="plan-list" aria-live="polite">
        <p className="plan-count"><b>{p.going(by("yes").length)}</b> · {p.maybes(by("maybe").length)} · {p.nos(by("no").length)}</p>
        {data.votes.length === 0 ? <p className="msg">{p.nobody}</p> : ANSWERS.map((a) => by(a).length > 0 && (
          <div key={a} className={`plan-group ${a}`}>
            <h3>{p[a]}</h3>
            <ul>{by(a).map((v, i) => <li key={i} dir="auto">{v.name} {v.mine && <small>{p.you}</small>}</li>)}</ul>
          </div>
        ))}
      </section>

      <section className="plan-next">
        <h2>{p.next}</h2>
        <a className="buy" href={`${e.url}?at=buy`} target="_blank" rel="noopener noreferrer">{p.buy} ↗</a>
        <div className="e-actions">
          {e.start_at && <AddToCalendar e={e} start={e.start_at} />}
          {coming.length > 0 && (
            <a className="f-pill" href={wa(p.groupMessage(e.title, coming))} target="_blank" rel="noopener noreferrer" title={p.groupHint}>
              {p.openGroup}
            </a>
          )}
          <Link className="f-pill" to={eventPath(e)}>{p.toEvent}</Link>
        </div>
        {coming.length > 0 && <p className="buy-note">{p.groupHint}</p>}
      </section>
    </main>
  );
}
