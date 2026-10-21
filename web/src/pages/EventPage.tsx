import { Link, useNavigate, useParams } from "react-router-dom";
import { useEvent } from "../api";
import { genreLabel, type Strings } from "../i18n";
import type { Ticket } from "../types";
import { artistPath, eventPath, fmtDate, freshness, Img, label, money, priceLabel, Share, sourceName, Sources, useT, useTitle, venuePath } from "../ui";

export default function EventPage() {
  const { id = "" } = useParams();
  const { t, lang } = useT();
  const navigate = useNavigate();
  const { data: e, isError, isPending } = useEvent(id);
  useTitle(e?.title);
  const back = () => (history.state?.idx > 0 ? navigate(-1) : navigate("/"));

  if (isError) return <p className="msg wrap">{t.error}</p>;
  if (isPending) return <p className="msg wrap">{t.loading}</p>;

  const images = e.images.map((i) => i.stored_url ?? i.url);
  const hero = images[0] ?? e.image_url;
  const mapQuery = e.latitude != null ? `${e.latitude},${e.longitude}` : [e.venue_name, e.address, e.city].filter(Boolean).join(", ");
  const price = priceLabel(e, t);
  const time = { hour: "2-digit", minute: "2-digit" } as const;

  return (
    <article className="event">
      <div className="e-backdrop" style={hero ? { backgroundImage: `url("${hero}")` } : undefined} aria-hidden />
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
            <Img src={hero} alt={e.title} className="e-img" />
          </div>

          <div className="e-info">
            <div className="tags">
              {e.genres.map((g) => <span key={g} className="tag">{genreLabel(lang, g)}</span>)}
              {!!e.min_age && <span className="tag age">{t.age(e.min_age)}</span>}
              <span className={"tag entry " + e.entry_type}>{label(t.entry, e.entry_type)}</span>
              {e.is_sold_out && <span className="tag sold">{t.soldOut}</span>}
            </div>
            <h1 dir="auto">{e.title}</h1>
            <Sources e={e} />

            <ul className="facts">
              <li><i>📅</i>{fmtDate(e.start_at, t, { weekday: "long", day: "numeric", month: "long", year: "numeric" })}</li>
              <li>
                <i>🕘</i>
                {/* Each time isolated, the range in the page's direction: in Hebrew the start sits on the right. */}
                <span dir={lang === "he" ? "rtl" : "ltr"}>
                  <bdi>{fmtDate(e.start_at, t, time)}</bdi>
                  {e.end_at && <> – <bdi>{fmtDate(e.end_at, t, time)}</bdi></>}
                  {e.time_slot && ` · ${label(t.slots, e.time_slot)}`}
                </span>
              </li>
              {(e.venue_name || e.address) && (
                <li>
                  <i>📍</i>
                  <span>
                    {e.venue_name ? <Link to={venuePath(e.venue_name)}>{e.venue_name}</Link> : null}
                    {[e.venue_name ? "" : null, e.address, e.city].filter((x) => x != null && x !== undefined).join(" · ")}{" "}
                    {mapQuery && (
                      <a href={`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(mapQuery)}`} target="_blank" rel="noopener noreferrer">
                        {t.map} ↗
                      </a>
                    )}
                  </span>
                </li>
              )}
              {price && (
                <li><i>🎟</i>{price}{e.max_price && e.max_price !== e.min_price ? ` – ${money(e.max_price, e.currency, t.locale)}` : ""}</li>
              )}
              {e.organizer && <li><i>🎤</i>{t.organizer}: {e.organizer}</li>}
            </ul>

            <a className="buy" href={e.url} target="_blank" rel="noopener noreferrer">
              {t.buy} ↗ <small>{sourceName(e.source)}</small>
            </a>
            <p className="buy-note">{t.buyNote} <Link to="/terms">{t.terms}</Link></p>
            <Share path={eventPath(e)} title={e.title} />
            {!!e.alternatives?.length && (
              <div className="alts">
                <span>{t.alsoAt}</span>
                {e.alternatives.map((a) => (
                  <a key={a.id} className="alt" href={a.url} target="_blank" rel="noopener noreferrer">
                    <b>{sourceName(a.source)}</b>
                    <small>{a.is_sold_out ? t.soldOut : priceLabel({ ...a, currency: e.currency }, t) || "—"}</small> ↗
                  </a>
                ))}
              </div>
            )}
          </div>
        </div>

        {e.lineup.length > 0 && (
          <section>
            <h4>{t.lineup}</h4>
            <div className="lineup-list">
              {e.lineup.map((a) => <Link key={a} to={artistPath(a)}>{a}</Link>)}
            </div>
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

        {e.description && (
          <section>
            <h4>{t.about}</h4>
            <p className="desc" dir="auto">{e.description}</p>
          </section>
        )}

        {images.length > 1 && (
          <section>
            <h4>{t.gallery}</h4>
            <div className="gallery">
              {images.slice(1).map((src) => <Img key={src} src={src} alt="" className="g-img" />)}
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
        <a className="buy" href={e.url} target="_blank" rel="noopener noreferrer">{t.buy} ↗</a>
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
