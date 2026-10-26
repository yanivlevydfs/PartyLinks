// Venue / artist pages and their index lists.
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useCategories, useEvents } from "../api";
import type { Facet } from "../types";
import { artistPath, Chip, EventGrid, Hero, Img, label, Loader, Related, useT, useTitle, venuePath } from "../ui";

const PAGE = 60;

function FacetIndex({ title, facets, path, byArea }: {
  title: string; facets: Facet[] | undefined; path: (v: string) => string; byArea?: boolean;
}) {
  const { t } = useT();
  const [q, setQ] = useState("");
  const [area, setArea] = useState<string | null>(null);
  // Thousands of cards at once froze the page: render a page at a time; search still covers the whole list.
  const [limit, setLimit] = useState(PAGE);
  const areas = byArea ? [...new Set((facets ?? []).map((f) => f.area).filter((a): a is string => !!a))] : [];
  useTitle(title);
  // Case-insensitive, ignores spaces/punctuation, so "gray club" finds "Gray Club תל אביב-יפו".
  const norm = (s: string) => s.toLocaleLowerCase().replace(/[\s\-_.·'"׳״]/g, "");
  const inArea = facets?.filter((f) => !area || f.area === area);
  const match = (s: string) => inArea?.filter((f) => norm(f.value).includes(s)) ?? [];
  const exact = match(norm(q));
  // A typo at the end ("יניבדד") finds nothing: drop letters from the end until names match (3 letters at least).
  let shown = exact;
  for (let s = norm(q); facets && !shown.length && s.length > 3; ) shown = match((s = s.slice(0, -1)));
  const fuzzy = exact.length === 0 && shown.length > 0;
  return (
    <>
      <Hero title={title}>
        <input className="search" type="search" value={q} onChange={(e) => { setQ(e.target.value); setLimit(PAGE); }} placeholder={t.searchList} aria-label={t.searchList} />
      </Hero>
      <main className="wrap">
        {areas.length > 0 && (
          <div className="chips filters" role="group" aria-label={t.area}>
            <Chip on={!area} onClick={() => { setArea(null); setLimit(PAGE); }}>📍 {t.allAreas}</Chip>
            {areas.map((a) => (
              <Chip key={a} on={area === a} onClick={() => { setArea(area === a ? null : a); setLimit(PAGE); }}>{label(t.areas, a)}</Chip>
            ))}
          </div>
        )}
        {!facets && <Loader />}
        {facets && !shown.length && <p className="msg">{q ? t.noMatch(q) : t.empty}</p>}
        {fuzzy && <p className="msg">{t.closest(q)}</p>}
        <div className="facet-grid">
          {shown.slice(0, limit).map((f) => (
            <Link key={f.value} className={"facet" + (f.image_url ? " with-photo" : "")} to={path(f.value)}>
              {f.image_url && <Img src={f.image_url} alt="" className="facet-photo" />}
              <b dir="auto">{f.value}{f.verified && <Verified />}</b>
              <small>{t.events(f.events)}{f.area ? ` · ${label(t.areas, f.area)}` : ""}{f.kind ? ` · ${f.kind.toUpperCase()}` : ""}</small>
            </Link>
          ))}
        </div>
        {shown.length > limit && (
          <div className="more">
            <button onClick={() => setLimit(limit + PAGE)}>{t.loadMore}</button>
          </div>
        )}
      </main>
    </>
  );
}

export function VenuesIndex() {
  const { t } = useT();
  return <FacetIndex title={t.venuesTitle} facets={useCategories({ anywhere: "true" }).data?.venues} path={venuePath} byArea />;
}

export function ArtistsIndex() {
  const { t } = useT();
  return <FacetIndex title={t.artistsTitle} facets={useCategories({ anywhere: "true" }).data?.lineup} path={artistPath} />;
}

export function VenuePage() {
  const { name = "" } = useParams();
  const { t } = useT();
  useTitle(name);
  // Shares the query cache with the grid below, so this costs no extra request.
  const first = useEvents({ venue: name }).data?.pages[0]?.items[0];
  const where = first && [first.address, first.city].filter(Boolean).join(" · ");
  const mapQuery = first && (first.latitude != null ? `${first.latitude},${first.longitude}` : [name, where].join(", "));
  return (
    <>
      <Hero title={name}>
        {where && <p className="sub">📍 {where}</p>}
        {mapQuery && (
          <a className="sub" href={`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(mapQuery)}`} target="_blank" rel="noopener noreferrer">
            {t.map} ↗
          </a>
        )}
      </Hero>
      <main className="wrap">
        <h4 className="section-title">{t.upcomingAt}</h4>
        <EventGrid params={{ venue: name }} />
        <Related path={`/venue/${encodeURIComponent(name)}`} />
      </main>
    </>
  );
}

function Verified() {
  const { t } = useT();
  return <span className="verified" title={t.verified} aria-label={t.verified}>✓</span>;
}

export function ArtistPage() {
  const { name = "" } = useParams();
  const { t } = useT();
  useTitle(name);
  // Same query cache as the artists index: no extra request.
  const profile = useCategories({ anywhere: "true" }).data?.lineup.find((f) => f.verified && f.value.toLowerCase() === name.toLowerCase());
  return (
    <>
      <Hero title={profile?.value ?? name}>
        {profile?.image_url && <Img src={profile.image_url} alt={profile.value} className="artist-photo" />}
        <p className="sub">
          🎧 {[profile?.kind?.toUpperCase(), profile?.genre].filter(Boolean).join(" · ") || t.lineup}
          {profile?.verified && <Verified />}
        </p>
        {[profile?.spotify_url, ...(profile?.links ?? []), profile?.profile_url].filter((u): u is string => !!u && /^https?:\/\//i.test(u)).map((u) => (
          <a key={u} className="sub" href={u} target="_blank" rel="noopener noreferrer">{siteName(u)} ↗</a>
        ))}
      </Hero>
      <main className="wrap">
        <h4 className="section-title">{t.upcomingAt}</h4>
        <EventGrid params={{ artist: name }} />
      </main>
    </>
  );
}

// "https://www.instagram.com/x" -> "instagram"
function siteName(url: string): string {
  try {
    return new URL(url).hostname.replace(/^(www|m|open|he|en)\./, "").split(".")[0];
  } catch {
    return url;
  }
}
