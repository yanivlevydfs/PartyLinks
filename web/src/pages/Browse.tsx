// Venue / artist pages and their index lists.
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useCategories, useEvents } from "../api";
import type { Facet } from "../types";
import { artistPath, Chip, EventGrid, Hero, label, useT, useTitle, venuePath } from "../ui";

function FacetIndex({ title, facets, path, byArea }: {
  title: string; facets: Facet[] | undefined; path: (v: string) => string; byArea?: boolean;
}) {
  const { t } = useT();
  const [q, setQ] = useState("");
  const [area, setArea] = useState<string | null>(null);
  const areas = byArea ? [...new Set((facets ?? []).map((f) => f.area).filter((a): a is string => !!a))] : [];
  useTitle(title);
  // Case-insensitive, ignores spaces/punctuation, so "gray club" finds "Gray Club תל אביב-יפו".
  const norm = (s: string) => s.toLocaleLowerCase().replace(/[\s\-_.·'"׳״]/g, "");
  const shown = facets?.filter((f) => norm(f.value).includes(norm(q)) && (!area || f.area === area));
  return (
    <>
      <Hero title={title}>
        <input className="search" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t.searchList} aria-label={t.searchList} />
      </Hero>
      <main className="wrap">
        {areas.length > 0 && (
          <div className="chips filters" role="group" aria-label={t.area}>
            <Chip on={!area} onClick={() => setArea(null)}>📍 {t.allAreas}</Chip>
            {areas.map((a) => (
              <Chip key={a} on={area === a} onClick={() => setArea(area === a ? null : a)}>{label(t.areas, a)}</Chip>
            ))}
          </div>
        )}
        {!facets && <p className="msg">{t.loading}</p>}
        {shown?.length === 0 && <p className="msg">{t.empty}</p>}
        <div className="facet-grid">
          {shown?.map((f) => (
            <Link key={f.value} className="facet" to={path(f.value)}>
              <b dir="auto">{f.value}</b>
              <small>{t.events(f.events)}{f.area ? ` · ${label(t.areas, f.area)}` : ""}</small>
            </Link>
          ))}
        </div>
      </main>
    </>
  );
}

export function VenuesIndex() {
  const { t } = useT();
  return <FacetIndex title={t.venuesTitle} facets={useCategories().data?.venues} path={venuePath} byArea />;
}

export function ArtistsIndex() {
  const { t } = useT();
  return <FacetIndex title={t.artistsTitle} facets={useCategories().data?.lineup} path={artistPath} />;
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
      </main>
    </>
  );
}

export function ArtistPage() {
  const { name = "" } = useParams();
  const { t } = useT();
  useTitle(name);
  return (
    <>
      <Hero title={name}><p className="sub">🎧 {t.lineup}</p></Hero>
      <main className="wrap">
        <h4 className="section-title">{t.upcomingAt}</h4>
        <EventGrid params={{ artist: name }} />
      </main>
    </>
  );
}
