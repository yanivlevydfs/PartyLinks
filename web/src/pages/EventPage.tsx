import { SponsorButton } from "../ads/SponsorSheet";
import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { SaveAlertButton } from "../alerts/SaveAlertButton";
import { useAlertText } from "../alerts/i18n";
import { isGone, useEvent } from "../api";
import { genreLabel, type Strings } from "../i18n";
import type { Alternative, Ticket } from "../types";
import { forecastAt, Weather } from "../Weather";
import { PlanButton } from "../plans/PlanButton";
import { EveningPlan } from "../evening/EveningPlan";
import { EventText } from "../event_text/EventText";
import { PlaceInfo } from "../places/PlaceInfo";
import { MapLinks } from "../places/MapLinks";
import { SaveButton } from "../saved/SaveButton";
import { EventPinButton } from "../home_pins/PinButtons";
import { EventStats } from "../popularity/EventStats";
import { LineupPlayer } from "../artist_links/ArtistPlayer";
import { AddToCalendar, countryName, eventPath, freshness, Img, label, Loader, money, priceLabel, Share, sized, sourceName, Sources, useT, useTitle, venuePath, when } from "../ui";

export default function EventPage() {
  const { id = "" } = useParams();
  const { t, lang } = useT();
  const navigate = useNavigate();
  const { data: e, error, isError, isPending } = useEvent(id);
  useTitle(e?.title);
  // One address per event: an old /event/<slug> link, or a duplicate listing's, shows as its short /e/<code>.
  const location = useLocation();
  const canonical = e?.code ? eventPath(e) : null;
  useEffect(() => {
    if (canonical && location.pathname !== canonical) navigate(canonical + location.search + location.hash, { replace: true });
  }, [canonical]); // eslint-disable-line react-hooks/exhaustive-deps
  const back = () => (history.state?.idx > 0 ? navigate(-1) : navigate("/"));
  const alertText = useAlertText();
  const [more, setMore] = useState(false);

  if (isError && isGone(error)) {
    // 410: the event is over (past events are purged); 404: no such event.
    const ended = error.message.startsWith("410");
    return (
      <main className="wrap">
        <p className="msg">{ended ? t.eventEnded : t.eventMissing}</p>
        <Link className="f-pill" to="/">{t.eventGoneCta}</Link>
      </main>
    );
  }
  if (isError) return <p className="msg wrap">{t.error}</p>;
  if (isPending) return <Loader />;

  const images = e.images.map((i) => i.stored_url ?? i.url);
  const hero = images[0] ?? e.image_url ?? e.cover_url;
  // Sites often add a tiny share-preview copy of the poster (a WhatsApp thumbnail); it isn't gallery material.
  // ponytail: size heuristic, not a content match; perceptual-hash dedup at download if real extras get dropped.
  const gallery = e.images.slice(1).filter((i) => !i.size_bytes || i.size_bytes > 40_000).map((i) => i.stored_url ?? i.url);
  // The best offer across the duplicate listings, as on the cards: the page said "from ₪359" (tickchak) while
  // Muzi and Ticketmaster, badged "Cheapest" below it, sold from ₪199 (Berry Sakharof, 2026-11-11).
  const price = priceLabel(
    {
      ...e,
      min_price: e.group_min_price ?? e.min_price,
      has_free_entry: e.group_has_free_entry ?? e.has_free_entry,
      entry_type: e.group_entry_type ?? e.entry_type,
    },
    t,
    e.tickets,
  );
  // The other ticket site with the lowest price, when it beats the main listing's: badged "Cheapest".
  // One card per site (a party often has several promoter links on the same site): its cheapest priced one.
  // Second-hand marketplaces (viagogo, SwapTix...) apart from the box offices; "Cheapest" only among box offices.
  const perSite = [...(e.alternatives ?? [])]
    .filter((a) => a.source !== e.source)
    .sort((a, b) => Number(a.is_sold_out) - Number(b.is_sold_out) || (a.min_price ?? Infinity) - (b.min_price ?? Infinity))
    .filter((a, i, all) => all.findIndex((b) => b.source === a.source) === i);
  const firsthand = perSite.filter((a) => !a.resale);
  const secondhand = perSite.filter((a) => a.resale && !a.is_sold_out); // viagogo often has no seller at all
  const cheapest = firsthand.filter((a) => !a.is_sold_out && a.min_price != null && (e.min_price == null || a.min_price < e.min_price))
    .sort((a, b) => a.min_price! - b.min_price!)[0]?.id;
  const time = { hour: "2-digit", minute: "2-digit" } as const;
  const liveTickets = e.tickets.filter((k) => k.status !== "sold_out");
  const goneTickets = e.tickets.filter((k) => k.status === "sold_out");
  // "Cheapest" on a ticket only when there is a choice: two on-sale prices that differ.
  const onSalePriced = liveTickets.filter((k) => k.status === "on_sale" && (k.is_free || k.price != null));
  const ticketPrice = (k: Ticket) => (k.is_free ? 0 : k.price!);
  const cheapTicket = new Set(onSalePriced.map(ticketPrice)).size > 1
    ? onSalePriced.reduce((a, b) => (ticketPrice(b) < ticketPrice(a) ? b : a))
    : undefined;

  return (
    <article className="event">
      <div className="e-backdrop" style={hero ? { backgroundImage: `url("${sized(hero, 640)}")` } : undefined} aria-hidden />
      <div className="wrap e-wrap">
        <button className="back" onClick={back}>{lang === "he" ? "→" : "←"} {t.back}</button>

        {!e.is_listed && <p className="e-notice warn" role="status">⚠ {t.unlisted}</p>}
        {freshness(e) === "updated" && e.offers_changed_at && (
          <p className="e-notice" role="status">
            ↻ {t.updatedAgo(Math.floor((Date.now() - new Date(e.offers_changed_at).getTime()) / 3600000))}
          </p>
        )}

        <div className="e-top">
          <Poster hero={hero} more={gallery} title={e.title} />

          <div className="e-info">
            <div className="tags">
              {e.genres.map((g) => <span key={g} className="tag">{genreLabel(lang, g)}</span>)}
              {!!e.min_age && <span className="tag age">{t.age(e.min_age)}</span>}
              {e.entry_type !== "unknown" && <span className={"tag entry " + e.entry_type}>{label(t.entry, e.entry_type)}</span>}
              {e.is_sold_out && <span className="tag sold">{t.soldOut}</span>}
            </div>
            <h1 dir="auto">{e.title}</h1>
            <Sources e={e} />
            <EventStats e={e} />

            <ul className="facts">
              <li><i>📅</i>{when(e, t, { weekday: "long", day: "numeric", month: "long", year: "numeric" })}</li>
              {e.time_known !== false && <li>
                <i>🕘</i>
                {/* Each time isolated, the range in the page's direction: in Hebrew the start sits on the right. */}
                <span dir={lang === "he" ? "rtl" : "ltr"}>
                  <bdi>{when(e, t, time)}</bdi>
                  {e.end_at && <> – <bdi>{when(e, t, time, e.end_at)}</bdi></>}
                  {e.time_slot && ` · ${label(t.slots, e.time_slot)}`}
                </span>
              </li>}
              <Weather lat={e.latitude} lng={e.longitude} city={e.city} start={forecastAt(e)} zone={e.time_zone} full />
              {(e.venue_name || e.address) && (
                <li>
                  <i>📍</i>
                  <span>
                    {e.venue_name ? <Link to={venuePath(e.venue_name)}>{e.venue_name}</Link> : null}
                    {[e.venue_name ? "" : null, e.address, e.city, countryName(e.country, lang) || null].filter((x) => x != null && x !== undefined).join(" · ")}{" "}
                    <MapLinks place={e} label={t.map} />
                  </span>
                </li>
              )}
              {price && (
                <li className="f-price"><i>🎟</i>{price}{e.max_price && e.max_price !== e.min_price ? ` – ${money(e.max_price, e.currency, t.locale)}` : ""}</li>
              )}
              {e.organizer && <li><i>🎤</i>{t.organizer}: {e.organizer}</li>}
            </ul>

            {/* The ticket links in one block: the main site, the other box offices, second-hand folded, the terms. */}
            <div className="buy-box">
              <a className="buy" href={`${e.url}?at=buy`} target="_blank" rel="noopener noreferrer">
                {t.buy} ↗ <small>{sourceName(e.source)}{e.extra?.resale ? ` · ${t.resaleTag}` : ""}</small>
              </a>
              {firsthand.length > 0 && (
                <div className="alts">
                  <span>{t.alsoAt}</span>
                  {firsthand.map((a) => <AltLink key={a.id} a={a} cheap={a.id === cheapest} currency={e.currency} t={t} />)}
                </div>
              )}
              {secondhand.length > 0 && (
                <details className="alts-used">
                  <summary>{t.resaleN(secondhand.length)}</summary>
                  <div className="alts used">
                    {secondhand.map((a) => <AltLink key={a.id} a={a} cheap={false} currency={e.currency} t={t} />)}
                  </div>
                </details>
              )}
              <p className="buy-note">{t.buyNote} <Link to="/terms">{t.terms}</Link></p>
            </div>
            {/* Save and share in reach; the calendar and a group plan behind "More". */}
            <div className="e-actions">
              <SaveButton eventId={e.id} large />
              <Share path={eventPath(e)} title={e.title} />
              <button type="button" className="e-more" aria-expanded={more} onClick={() => setMore(!more)}>⋯ {t.moreActions}</button>
              {more && (
                <div className="e-more-row">
                  {e.start_at && <AddToCalendar e={e} start={e.start_at} />}
                  <PlanButton eventId={e.id} />
                </div>
              )}
            </div>
            {/* Admin only (each renders nothing for a visitor). */}
            <div className="e-admin">
              <EventPinButton event={e} />
              <SponsorButton event={e} />
            </div>
          </div>
        </div>

        {e.description_layout && (
          <section>
            <h4>{t.about}</h4>
            <EventText layout={e.description_layout} />
          </section>
        )}

        {e.place && <PlaceInfo id={e.id} />}

        {e.lineup.length > 0 && (
          <section>
            <h4>{t.lineup}</h4>
            <LineupPlayer names={e.lineup} links={e.lineup_links}>
              <SaveAlertButton q={e.lineup[0]} label={alertText.artist(e.lineup[0])} />
            </LineupPlayer>
          </section>
        )}

        {e.tickets.length > 0 && (
          <section>
            <h4>{t.tickets}</h4>
            <ul className="tickets">
              {(goneTickets.length && liveTickets.length ? liveTickets : e.tickets).map((k, i) => (
                <TicketRow key={k.source_ticket_id ?? i} k={k} t={t} cheap={k === cheapTicket} />
              ))}
            </ul>
            {/* Sold-out tickets fold into one line, unless nothing else is left to show. */}
            {goneTickets.length > 0 && liveTickets.length > 0 && (
              <details className="t-gone">
                <summary>{t.soldOutN(goneTickets.length)}</summary>
                <ul className="tickets">
                  {goneTickets.map((k, i) => <TicketRow key={k.source_ticket_id ?? i} k={k} t={t} />)}
                </ul>
              </details>
            )}
          </section>
        )}

        <EveningPlan eventId={e.id} />
      </div>
      {/* Phones: price + the ticket link stay in reach while reading lineup, tickets and description. */}
      <div className="buy-bar">
        <div>
          <b>{e.is_sold_out ? t.soldOut : price}</b>
          <small>{sourceName(e.source)}</small>
        </div>
        <a className="buy" href={`${e.url}?at=bar`} target="_blank" rel="noopener noreferrer">{t.buy} ↗</a>
      </div>
    </article>
  );
}

/** The poster, and the event's other pictures behind it: swipe (or the dots) through them. */
function Poster({ hero, more, title }: { hero: string | null | undefined; more: string[]; title: string }) {
  const { t } = useT();
  const strip = useRef<HTMLDivElement>(null);
  const [at, setAt] = useState(0);
  const n = more.length + 1;
  const go = (i: number) => {
    const el = strip.current;
    const slide = el?.children[i] as HTMLElement | undefined;
    if (el && slide) el.scrollTo({ left: slide.offsetLeft - el.offsetLeft, behavior: "smooth" });
  };
  const onScroll = () => {
    const el = strip.current;
    if (el) setAt(Math.round(Math.abs(el.scrollLeft) / el.clientWidth));
  };
  return (
    <div className="e-poster">
      <div className="e-shots" ref={strip} onScroll={n > 1 ? onScroll : undefined}>
        <Img src={sized(hero, 1280)} alt={title} className="e-img" text={title} eager />
        {more.map((src) => <Img key={src} src={sized(src, 1280)} alt="" className="e-img" />)}
      </div>
      {n > 1 && (
        <div className="e-dots">
          {Array.from({ length: n }, (_, i) => (
            <button key={i} type="button" className={i === at ? "on" : undefined} aria-label={t.photoN(i + 1, n)}
              aria-current={i === at || undefined} onClick={() => go(i)} />
          ))}
        </div>
      )}
    </div>
  );
}

function AltLink({ a, cheap, currency, t }: { a: Alternative; cheap: boolean; currency: string; t: Strings }) {
  return (
    <a className={"alt" + (cheap ? " cheap" : "")} href={`${a.url}?at=alt`} target="_blank" rel="noopener noreferrer">
      <b>{sourceName(a.source)}</b>
      <small>{a.is_sold_out ? t.soldOut : priceLabel({ ...a, currency }, t, a.tickets) || t.priceOnSite}</small>
      {cheap && <em>{t.cheapest}</em>} ↗
    </a>
  );
}

function TicketRow({ k, t, cheap = false }: { k: Ticket; t: Strings; cheap?: boolean }) {
  const price = k.is_free || k.price === 0 ? t.free : k.price != null ? money(k.price, k.currency, t.locale) : "";
  const notes = [
    label(t.status, k.status),
    k.pay_at_door && t.payAtDoor,
    k.admits > 1 && t.admits(k.admits),
    k.remaining != null && t.remaining(k.remaining),
    k.price_rounds.length > 1 && `${t.rounds}: ${k.price_rounds.map((p) => money(p, k.currency, t.locale)).join(" → ")}`,
  ].filter(Boolean);
  return (
    <li className={"ticket" + (k.status !== "on_sale" ? " off" : "") + (cheap ? " cheap" : "")}>
      <div>
        <b dir="auto">{k.name}</b>
        <small>{notes.join(" · ")}</small>
        {k.description && <small dir="auto">{k.description}</small>}
      </div>
      <div className="t-price">
        <b>{price}</b>
        {cheap && <em>{t.cheapest}</em>}
        {k.service_fee ? <small>+{money(k.service_fee, k.currency, t.locale)} {t.fee}</small> : null}
      </div>
    </li>
  );
}
