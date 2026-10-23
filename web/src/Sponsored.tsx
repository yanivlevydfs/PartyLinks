// Paid placements (picked at /admin#sponsored), on every page: a rail fixed to each side of the
// screen on wide screens, one sideways strip above the footer on narrower ones.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { useSponsored } from "./api";
import type { EventSummary } from "./types";
import { eventPath, fmtDate, Img, label, priceLabel, useT } from "./ui";
import "./sponsored.css";

type Side = "right" | "left";
// Seconds per spotlight; out of step so the two rails never flip together.
const ROTATE_S: Record<Side, number> = { right: 6, left: 7.5 };

// For the advertiser report (admin): a view is counted once per event per visit, every click counts.
const viewed = new Set<string>();
function hit(eventId: string, kind: "view" | "click") {
  if (kind === "view") {
    if (viewed.has(eventId)) return;
    viewed.add(eventId);
  }
  try {
    navigator.sendBeacon("/events/sponsored/hit", new Blob([JSON.stringify({ event_id: eventId, kind })], { type: "application/json" }));
  } catch {}
}

/** Every page below the header: narrowed between the two rails (only when something is sponsored). */
export function SponsoredFrame({ children }: { children: ReactNode }) {
  const rails = useSponsored().data;
  const admin = useLocation().pathname.startsWith("/admin");
  const on = !!rails && rails.right.length + rails.left.length > 0 && !admin;
  return (
    <div className={"sp-page" + (on ? " on" : "")}>
      {on && <Rail side="right" items={rails.right} />}
      {on && <Rail side="left" items={rails.left} />}
      {children}
    </div>
  );
}

/** Every sponsored event in one row, for screens too narrow for the rails. */
export function SponsoredStrip() {
  const { t } = useT();
  const rails = useSponsored().data;
  const admin = useLocation().pathname.startsWith("/admin");
  const items = rails ? [...rails.right, ...rails.left] : [];
  if (!items.length || admin) return null;
  return (
    <section className="sp-strip wrap" aria-label={t.sponsored}>
      <Head />
      <div className="sp-strip-row">
        {items.map((e) => <Spot key={e.id} e={e} on />)}
      </div>
    </section>
  );
}

function Head() {
  const { t } = useT();
  return <div className="sp-head"><span className="sp-pulse" aria-hidden />{t.sponsored}</div>;
}

/** One side: a rotating spotlight (paused on hover), its progress, and the side's whole list. */
function Rail({ side, items }: { side: Side; items: EventSummary[] }) {
  const { t } = useT();
  const [active, setActive] = useState(0);
  if (!items.length) return <aside className="sp-rail" data-side={side} aria-hidden />;
  const i = active % items.length;
  const next = () => setActive((a) => (a + 1) % items.length);
  return (
    <aside className="sp-rail" data-side={side} aria-label={t.sponsored}>
      <Head />
      <div className="sp-frame">
        <div className="sp-stage">
          {items.map((e, n) => <Spot key={e.id} e={e} on={n === i} />)}
        </div>
      </div>
      {items.length > 1 && (
        <>
          <div className="sp-progress" aria-hidden>
            {items.map((e, n) => (
              <span key={e.id} className={n < i ? "done" : ""}>
                {n === i && <i key={active} style={{ animationDuration: `${ROTATE_S[side]}s` }} onAnimationEnd={next} />}
              </span>
            ))}
          </div>
          <ul className="sp-list">
            {items.map((e, n) => (
              <li key={e.id}>
                <Link to={eventPath(e)} className={n === i ? "on" : ""} onMouseEnter={() => setActive(n)} onFocus={() => setActive(n)}
                  onClick={() => hit(e.id, "click")}>
                  <Img src={e.cover_url ?? e.image_url} alt="" className="sp-thumb" />
                  <span>
                    <b dir="auto">{e.title}</b>
                    <small>{fmtDate(e.start_at, t, { weekday: "short", day: "numeric", month: "numeric" })}</small>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </>
      )}
    </aside>
  );
}

function Spot({ e, on }: { e: EventSummary; on: boolean }) {
  const { t } = useT();
  // The best offer across duplicate listings, as on the cards.
  const price = priceLabel(
    {
      ...e,
      min_price: e.group_min_price ?? e.min_price,
      has_free_entry: e.group_has_free_entry ?? e.has_free_entry,
      entry_type: e.group_entry_type ?? e.entry_type,
    },
    t,
  );
  const where = [e.venue_name, e.area && label(t.areas, e.area)].filter(Boolean).join(" · ");
  // A view = the shown card at least half on screen (hidden rails and strips never intersect).
  const ref = useRef<HTMLAnchorElement>(null);
  useEffect(() => {
    if (!on || !ref.current) return;
    const io = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        hit(e.id, "view");
        io.disconnect();
      }
    }, { threshold: 0.5 });
    io.observe(ref.current);
    return () => io.disconnect();
  }, [on, e.id]);
  return (
    <Link ref={ref} className={"sp-spot" + (on ? " on" : "")} to={eventPath(e)} tabIndex={on ? 0 : -1} aria-hidden={!on}
      onClick={() => hit(e.id, "click")}>
      <Img src={e.cover_url ?? e.image_url} alt={e.title} className="sp-img" text={e.title} />
      <div className="date-badge">
        <b>{fmtDate(e.start_at, t, { day: "numeric" })}</b>
        <span>{fmtDate(e.start_at, t, { month: "short" })}</span>
      </div>
      {e.is_sold_out ? (
        <span className="flag sold">{t.soldOut}</span>
      ) : (
        price && <span className={"flag" + (price === t.free ? " free" : "")}>{price}</span>
      )}
      <div className="sp-info">
        <h3 dir="auto">{e.title}</h3>
        <p>{fmtDate(e.start_at, t, { weekday: "short", day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" })}</p>
        {where && <p>📍 {where}</p>}
        <span className="sp-cta">{t.sponsoredCta} <i aria-hidden>→</i></span>
      </div>
    </Link>
  );
}
