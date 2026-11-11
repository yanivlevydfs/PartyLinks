import { Link, useNavigate, useParams } from "react-router-dom";
import { SaveAlertButton } from "../alerts/SaveAlertButton";
import { useAlertText } from "../alerts/i18n";
import { isGone, useEvent } from "../api";
import { genreLabel, type Strings } from "../i18n";
import type { Ticket } from "../types";
import { forecastAt, Weather } from "../Weather";
import { PlanButton } from "../plans/PlanButton";
import { EveningPlan } from "../evening/EveningPlan";
import { EventText } from "../event_text/EventText";
import { PlaceInfo } from "../places/PlaceInfo";
import { MapLinks } from "../places/MapLinks";
import { SaveButton } from "../saved/SaveButton";
import { EventStats } from "../popularity/EventStats";
import { LineupPlayer } from "../artist_links/ArtistPlayer";
import { AddToCalendar, countryName, eventPath, freshness, Img, label, Loader, money, priceLabel, Share, sized, sourceName, Sources, useT, useTitle, venuePath, when } from "../ui";

export default function EventPage() {
  const { id = "" } = useParams();
  const { t, lang } = useT();
  const navigate = useNavigate();
  const { data: e, error, isError, isPending } = useEvent(id);
  useTitle(e?.title);
  const back = () => (history.state?.idx > 0 ? navigate(-1) : navigate("/"));
  const alertText = useAlertText();

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
  const price = priceLabel(e, t, e.tickets);
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
          <div className="e-poster">
            <Img src={sized(hero, 1280)} alt={e.title} className="e-img" text={e.title} eager />
          </div>

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
                <li><i>🎟</i>{price}{e.max_price && e.max_price !== e.min_price ? ` – ${money(e.max_price, e.currency, t.locale)}` : ""}</li>
              )}
              {e.organizer && <li><i>🎤</i>{t.organizer}: {e.organizer}</li>}
            </ul>

            <a className="buy" href={`${e.url}?at=buy`} target="_blank" rel="noopener noreferrer">
              {t.buy} ↗ <small>{sourceName(e.source)}{e.extra?.resale ? ` · ${t.resaleTag}` : ""}</small>
            </a>
            {([[t.alsoAt, firsthand, ""], [t.resale, secondhand, " used"]] as const).map(([title, alts, kind]) => !!alts.length && (
              <div key={kind} className={"alts" + kind}>
                <span>{title}</span>
                {alts.map((a) => (
                  <a key={a.id} className={"alt" + (a.id === cheapest ? " cheap" : "")} href={`${a.url}?at=alt`} target="_blank" rel="noopener noreferrer">
                    <b>{sourceName(a.source)}</b>
                    <small>{a.is_sold_out ? t.soldOut : priceLabel({ ...a, currency: e.currency }, t, a.tickets) || t.priceOnSite}</small>
                    {a.id === cheapest && <em>{t.cheapest}</em>} ↗
                  </a>
                ))}
              </div>
            ))}
            <p className="buy-note">{t.buyNote} <Link to="/terms">{t.terms}</Link></p>
            <div className="e-actions">
              <Share path={eventPath(e)} title={e.title} />
              {e.start_at && <AddToCalendar e={e} start={e.start_at} />}
              <PlanButton eventId={e.id} />
              <SaveButton eventId={e.id} large />
            </div>
          </div>
        </div>

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
              {e.tickets.map((k, i) => <TicketRow key={k.source_ticket_id ?? i} k={k} t={t} />)}
            </ul>
          </section>
        )}

        <EveningPlan eventId={e.id} />

        {e.description_layout && (
          <section>
            <h4>{t.about}</h4>
            <EventText layout={e.description_layout} />
          </section>
        )}

        {gallery.length > 0 && (
          <section>
            <h4>{t.gallery}</h4>
            <div className="gallery">
              {gallery.map((src) => <Img key={src} src={sized(src, 640)} alt="" className="g-img" />)}
            </div>
          </section>
        )}
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

function TicketRow({ k, t }: { k: Ticket; t: Strings }) {
  const price = k.is_free || k.price === 0 ? t.free : k.price != null ? money(k.price, k.currency, t.locale) : "";
  const notes = [
    label(t.status, k.status),
    k.pay_at_door && t.payAtDoor,
    k.admits > 1 && t.admits(k.admits),
    k.remaining != null && t.remaining(k.remaining),
    k.price_rounds.length > 1 && `${t.rounds}: ${k.price_rounds.map((p) => money(p, k.currency, t.locale)).join(" → ")}`,
  ].filter(Boolean);
  return (
    <li className={"ticket" + (k.status !== "on_sale" ? " off" : "")}>
      <div>
        <b dir="auto">{k.name}</b>
        <small>{notes.join(" · ")}</small>
        {k.description && <small dir="auto">{k.description}</small>}
      </div>
      <div className="t-price">
        <b>{price}</b>
        {k.service_fee ? <small>+{money(k.service_fee, k.currency, t.locale)} {t.fee}</small> : null}
      </div>
    </li>
  );
}
