// /plan/:id: "who's coming with me?" for one event, or "which party?" between up to four. Friends
// open it from WhatsApp, write a name and answer (in / maybe / not this time), mark where they'd go
// and whether they drive or need a ride, no sign-up; the answers refresh while the page is open.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { SignInButton } from "../accounts/AccountSlot";
import { useAccountsOn, useMe } from "../accounts/useMe";
import type { EventSummary } from "../types";
import { AddToCalendar, eventPath, Img, Loader, sized, useT, useTitle, when } from "../ui";
import { forecastAt, Weather } from "../Weather";
import { AddOption } from "./AddOption";
import { FreeEntry, freeTime, freeUntil } from "./FreeEntry";
import { GroupName } from "./GroupName";
import { usePlanText } from "./i18n";
import "./plans.css";
import { WRITE_HEADERS, type Answer, type Plan, type Ride, type Vote } from "./types";

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
const LONG = { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" } as const;

export default function PlanPage() {
  const { id = "" } = useParams();
  const { t } = useT();
  const p = usePlanText();
  const client = useQueryClient();
  const key = ["plan", id];
  const { data, error, isPending } = useQuery({
    queryKey: key,
    queryFn: async () => {
      // The key goes in a header, never the URL (logs and proxies keep URLs).
      const res = await fetch(`/plans/${encodeURIComponent(id)}`, { headers: { "X-Plan-Voter": voter } });
      if (!res.ok) throw new Error(String(res.status));
      return (await res.json()) as Plan;
    },
    refetchInterval: 20_000, // friends answer while it is open; 3 requests a minute, well under the rate limit
    retry: false,
  });
  const mine = data?.votes.find((v) => v.mine);
  const accounts = useAccountsOn();
  const me = useMe().data;
  const [name, setName] = useState(() => stored("plan-name", () => ""));
  useEffect(() => { if (me && !name) setName(me.name.split(" ")[0] ?? ""); }, [me, name]);
  const [picks, setPicks] = useState<string[]>([]);
  const [ride, setRide] = useState<Ride | null>(null);
  const [seats, setSeats] = useState(3);
  const [note, setNote] = useState("");
  const [copied, setCopied] = useState(false);
  // This browser's saved answer fills the form once, when the plan first loads.
  const [filled, setFilled] = useState(false);
  useEffect(() => {
    if (!data || filled) return;
    setFilled(true);
    if (mine) {
      setName(mine.name);
      setPicks(mine.picks);
      setRide(mine.ride);
      if (mine.seats) setSeats(mine.seats);
    }
  }, [data, filled, mine]);
  // Back from signing in: this browser's answer joins the account once, so the group is in "my groups".
  const [claimed, setClaimed] = useState(false);
  useEffect(() => {
    if (!me || !mine || !data || data.in_groups || claimed) return;
    setClaimed(true);
    void fetch(`/plans/${encodeURIComponent(id)}/votes`, {
      method: "POST", headers: WRITE_HEADERS,
      body: JSON.stringify({ voter, name: mine.name, answer: mine.answer, picks: mine.picks, ride: mine.ride, seats: mine.seats ?? undefined }),
    }).then(async (res) => {
      if (!res.ok) return;
      client.setQueryData(key, await res.json());
      void client.invalidateQueries({ queryKey: ["groups"] });
    });
  }, [me, mine, data, claimed]); // eslint-disable-line react-hooks/exhaustive-deps
  const many = (data?.options.length ?? 0) > 1;
  useTitle(data ? (many ? `${p.which} ${data.options.map((o) => o.title).join(" / ")}` : `${p.title} ${data.event.title}`) : p.title);

  if (isPending) return <Loader />;
  if (error || !data) {
    return <main className="wrap plan"><p className="msg">{p.gone}</p><Link className="f-pill" to="/">SPOTX</Link></main>;
  }

  const e = data.event;
  const url = `${window.location.origin}/plan/${data.id}`;
  const whenText = when(e, t, LONG);
  const dayText = when(e, t, { weekday: "long", day: "numeric", month: "long" });
  const where = [e.venue_name, e.city].filter(Boolean).join(", ");
  const by = (a: Answer) => data.votes.filter((v) => v.answer === a);
  const coming = by("yes").map((v) => v.name);
  const counts = new Map(data.options.map((o) => [o.id, data.votes.filter((v) => v.answer !== "no" && v.picks.includes(o.id)).length]));
  const top = Math.max(0, ...counts.values());
  const drivers = data.votes.filter((v) => v.ride === "drive");
  const riders = data.votes.filter((v) => v.ride === "need");
  const free = drivers.reduce((n, v) => n + (v.seats ?? 0), 0);
  const closes = many ? null : freeUntil(e); // one event: its free entry's end goes in the first message too
  const message = many
    ? p.whichMessage(data.options.map((o) => o.title), dayText)
    : p.message(e.title, whenText, where, closes ? p.freeLine(freeTime(e, closes, t)) : "");
  const rideLines = [
    ...drivers.map((v) => `${v.name}: ${p.seatsOf(v.seats ?? 1)}`),
    ...(riders.length ? [`${p.riders}: ${riders.map((v) => v.name).join(", ")}`] : []),
  ];

  const save = async (next: { answer?: Answer; picks?: string[]; ride?: Ride | null; seats?: number }) => {
    const answer = next.answer ?? mine?.answer;
    if (!answer) return; // the form only saves once an answer is picked
    const clean = name.trim();
    if (!clean) return setNote(p.nameFirst);
    const chosen = next.picks ?? picks;
    if (many && answer !== "no" && chosen.length === 0) return setNote(p.pickOne);
    const r = next.ride !== undefined ? next.ride : ride;
    const res = await fetch(`/plans/${encodeURIComponent(id)}/votes`, {
      method: "POST", headers: WRITE_HEADERS,
      body: JSON.stringify({ voter, name: clean, answer, picks: many ? chosen : undefined, ride: r, seats: r === "drive" ? next.seats ?? seats : undefined }),
    });
    if (res.ok) {
      remember(clean);
      client.setQueryData(key, await res.json());
      void client.invalidateQueries({ queryKey: ["groups"] });
      setNote(p.saved);
    } else setNote(res.status === 409 ? p.full : res.status === 403 ? p.signInToChange : res.status === 429 ? p.slowDown : p.failed);
  };
  const toggle = (optionId: string) => {
    const next = picks.includes(optionId) ? picks.filter((x) => x !== optionId) : [...picks, optionId];
    setPicks(next);
    if (mine && next.length) void save({ picks: next });
  };
  const chooseRide = (r: Ride | null) => { setRide(r); if (mine) void save({ ride: r }); };
  const copy = () => navigator.clipboard?.writeText(url).then(() => setCopied(true), () => {});

  return (
    <main className="wrap plan">
      <GroupName plan={data} onSaved={(plan) => client.setQueryData<Plan>(key, plan)} />
      {many ? (
        <section className="plan-which" aria-labelledby="plan-which-h">
          <h1 id="plan-which-h">{p.which} <span>{dayText}</span></h1>
          <p className="plan-sub">{p.whichIntro}</p>
          <ol className="plan-options">
            {data.options.map((o) => (
              <Option key={o.id} o={o} votes={counts.get(o.id) ?? 0} leading={top > 0 && counts.get(o.id) === top} />
            ))}
          </ol>
        </section>
      ) : (
        <Link to={eventPath(e)} className="plan-event">
          <Img src={sized(e.cover_url ?? e.image_url, 640)} alt={e.title} className="plan-img" text={e.title} />
          <div>
            <h1 dir="auto">{p.title} <span>{e.title}</span></h1>
            <p>📅 {whenText}</p>
            {where && <p dir="auto">📍 {where}</p>}
            <ul className="facts"><Weather lat={e.latitude} lng={e.longitude} city={e.city} start={forecastAt(e)} zone={e.time_zone} full /></ul>
          </div>
        </Link>
      )}
      <FreeEntry events={data.options} planUrl={url} />
      <AddOption plan={data} onAdded={(plan) => client.setQueryData<Plan>(key, plan)} />

      <section className="plan-share">
        <p>{p.intro}</p>
        <a className="plan-wa" href={wa(`${message}\n${url}`)} target="_blank" rel="noopener noreferrer">{p.sendWhatsapp}</a>
        <div className="plan-more">
          {typeof navigator.share === "function" && (
            <button type="button" className="f-pill" onClick={() => navigator.share({ title: many ? p.which : `${p.title} ${e.title}`, url }).catch(() => {})}>{p.shareOther}</button>
          )}
          <button type="button" className="f-pill" onClick={copy}>{copied ? p.copied : p.copy}</button>
        </div>
      </section>

      <section className="plan-vote" aria-labelledby="plan-vote-h">
        <h2 id="plan-vote-h">{mine ? p.change : p.yourName}</h2>
        <input className="search" value={name} maxLength={40} onChange={(ev) => { setName(ev.target.value); setNote(""); }}
          placeholder={p.yourName} aria-label={p.yourName} autoComplete="given-name" />
        {many && (
          <fieldset className="plan-where">
            <legend>{p.where}</legend>
            {data.options.map((o, i) => (
              <label key={o.id} className={"plan-chip" + (picks.includes(o.id) ? " on" : "")}>
                <input type="checkbox" checked={picks.includes(o.id)} onChange={() => toggle(o.id)} />
                <span dir="auto">{i + 1}. {o.title}</span>
              </label>
            ))}
          </fieldset>
        )}
        <fieldset className="plan-where">
          <legend>{p.ride}</legend>
          {([["drive", p.drive], ["need", p.need], [null, p.noRide]] as const).map(([r, label]) => (
            <label key={String(r)} className={"plan-chip" + (ride === r ? " on" : "")}>
              <input type="radio" name="plan-ride" checked={ride === r} onChange={() => chooseRide(r)} />
              <span>{label}</span>
            </label>
          ))}
          {ride === "drive" && (
            <label className="plan-seats">
              {p.seats}
              <select value={seats} onChange={(ev) => { const n = Number(ev.target.value); setSeats(n); if (mine) void save({ seats: n }); }}>
                {[1, 2, 3, 4, 5, 6, 7, 8].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
          )}
        </fieldset>
        <div className="plan-answers" role="group" aria-label={p.title}>
          {ANSWERS.map((a) => (
            <button key={a} type="button" className={`plan-a ${a}` + (mine?.answer === a ? " on" : "")} aria-pressed={mine?.answer === a} onClick={() => save({ answer: a })}>
              {p[a]}
            </button>
          ))}
        </div>
        {note && <p className="plan-note" role="status">{note}</p>}
        {accounts && !me && (
          <div className="plan-keep">
            <p>{p.keepGroup}</p>
            <SignInButton next={`/plan/${data.id}`} />
          </div>
        )}
        {me && data.in_groups && <p className="plan-keep"><Link to="/groups">{p.inGroups} ›</Link></p>}
      </section>

      <section className="plan-list" aria-live="polite">
        <p className="plan-count"><b>{p.going(by("yes").length)}</b> · {p.maybes(by("maybe").length)} · {p.nos(by("no").length)}</p>
        {data.votes.length === 0 ? <p className="msg">{p.nobody}</p> : ANSWERS.map((a) => by(a).length > 0 && (
          <div key={a} className={`plan-group ${a}`}>
            <h3>{p[a]}</h3>
            <ul>{by(a).map((v, i) => <VoteChip key={i} v={v} options={data.options} many={many} you={p.you} />)}</ul>
          </div>
        ))}
        {(drivers.length > 0 || riders.length > 0) && (
          <div className="plan-group rides">
            <h3>{p.ride}</h3>
            {drivers.length > 0 && <p>🚗 {p.drivers}: {drivers.map((v) => `${v.name} (${p.seatsOf(v.seats ?? 1)})`).join(" · ")}</p>}
            {riders.length > 0 && <p>🙋 {p.riders}: {riders.map((v) => v.name).join(" · ")}</p>}
            {riders.length > 0 && <p className={free >= riders.length ? "ok" : "short"}>{p.seatsLeft(free, riders.length)}</p>}
          </div>
        )}
      </section>

      <section className="plan-next">
        <h2>{p.next}</h2>
        {!many && <a className="buy" href={`${e.url}?at=buy`} target="_blank" rel="noopener noreferrer">{p.buy} ↗</a>}
        <div className="e-actions">
          {!many && e.start_at && <AddToCalendar e={e} start={e.start_at} />}
          {coming.length > 0 && (
            <a className="f-pill" href={wa(p.groupMessage(many ? p.which : e.title, coming) + p.rides(rideLines))} target="_blank" rel="noopener noreferrer" title={p.groupHint}>
              {p.openGroup}
            </a>
          )}
          {!many && <Link className="f-pill" to={eventPath(e)}>{p.toEvent}</Link>}
        </div>
        {coming.length > 0 && <p className="buy-note">{p.groupHint}</p>}
      </section>
    </main>
  );
}

/** One event of a "which party?" plan: poster, time, place, its votes, tickets. */
function Option({ o, votes, leading }: { o: EventSummary; votes: number; leading: boolean }) {
  const { t } = useT();
  const p = usePlanText();
  return (
    <li className={"plan-option" + (leading ? " lead" : "")}>
      <Link to={eventPath(o)} className="plan-option-link">
        <Img src={sized(o.cover_url ?? o.image_url, 640)} alt={o.title} className="plan-thumb" />
        <div>
          <b dir="auto">{o.title}</b>
          <small>{when(o, t, { hour: "2-digit", minute: "2-digit" })}{o.venue_name ? ` · ${o.venue_name}` : ""}</small>
        </div>
      </Link>
      <span className="plan-votes">{leading && <em>{p.leading}</em>} {p.votesFor(votes)}</span>
      <a className="f-pill" href={`${o.url}?at=buy`} target="_blank" rel="noopener noreferrer">{p.buy} ↗</a>
    </li>
  );
}

function VoteChip({ v, options, many, you }: { v: Vote; options: EventSummary[]; many: boolean; you: string }) {
  const where = many ? v.picks.map((id) => options.findIndex((o) => o.id === id) + 1).filter((n) => n > 0).sort().join(",") : "";
  return (
    <li dir="auto">
      {v.name}{where && <small> · {where}</small>}{v.ride === "drive" && " 🚗"}{v.ride === "need" && " 🙋"} {v.mine && <small>{you}</small>}
    </li>
  );
}
