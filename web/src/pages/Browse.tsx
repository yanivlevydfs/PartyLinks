// Venue / artist pages and their index lists.
import { Link, useParams } from "react-router-dom";
import { useCategories, useEvents } from "../api";
import type { Facet } from "../types";
import { artistPath, EventGrid, Hero, useT, venuePath } from "../ui";

function FacetIndex({ title, facets, path }: { title: string; facets: Facet[] | undefined; path: (v: string) => string }) {
  const { t } = useT();
  return (
    <>
      <Hero title={title} />
      <main className="wrap">
        {!facets && <p className="msg">{t.loading}</p>}
        <div className="facet-grid">
          {facets?.map((f) => (
            <Link key={f.value} className="facet" to={path(f.value)}>
              <b dir="auto">{f.value}</b>
              <small>{t.events(f.events)}</small>
            </Link>
          ))}
        </div>
      </main>
    </>
  );
}

export function VenuesIndex() {
  const { t } = useT();
  return <FacetIndex title={t.venuesTitle} facets={useCategories().data?.venues} path={venuePath} />;
}

export function ArtistsIndex() {
  const { t } = useT();
  return <FacetIndex title={t.artistsTitle} facets={useCategories().data?.lineup} path={artistPath} />;
}

export function VenuePage() {
  const { name = "" } = useParams();
  const { t } = useT();
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
