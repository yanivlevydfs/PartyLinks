// Venue / artist pages and their index lists.
import { type ReactNode, useEffect, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { Link, useLocation, useParams } from "react-router-dom";
import { getJSON, type Params, toQuery, useCategories, useEvents, useLandingInfo } from "../api";
import type { Facet } from "../types";
import { ArtistLinks } from "../artist_links/ArtistLinks";
import { ArtistPlayer } from "../artist_links/ArtistPlayer";
import { SimilarArtists } from "../similar/SimilarArtists";
import { type ArtistChips, ArtistFilterRows, useArtistFilter } from "../artist_filter/ArtistFilter";
import { MapLinks } from "../places/MapLinks";
import { ScopedEvents } from "../filter_scope/ScopedEvents";
import { useWeb } from "../site_params/useWeb";
import { artistPath, Chip, countryName, Hero, Img, label, Loader, Related, SHARE_ICONS, useT, useTitle, venuePath } from "../ui";

// The name's first letter, drawn when there is no picture.
const initial = (s: string) => {
  const c = s.replace(/^[^\p{L}\p{N}]+/u, "").charAt(0);
  return /\p{L}/u.test(c) ? c.toLocaleUpperCase() : "#"; // "1-800-LoLitA": not a big "1"
};

/** One page of GET /facets/venues or /facets/artists. */
interface FacetPage { items: Facet[]; total: number; page: number; fuzzy: boolean; areas?: string[]; chips?: ArtistChips }

type View = "list" | "grid";
const VIEW_KEY = "facetView";
function savedView(): View {
  try {
    const v = localStorage.getItem(VIEW_KEY);
    if (v === "list" || v === "grid") return v;
  } catch { /* private mode */ }
  return matchMedia("(max-width: 760px)").matches ? "list" : "grid"; // phones read names, desktops see pictures
}

/** List / grid switch: two icon buttons, the choice remembered on this device. */
function ViewToggle({ view, set }: { view: View; set: (v: View) => void }) {
  const { t } = useT();
  const pick = (v: View) => {
    set(v);
    try { localStorage.setItem(VIEW_KEY, v); } catch { /* private mode */ }
  };
  return (
    <div className="view-toggle" role="group">
      <button type="button" aria-pressed={view === "list"} aria-label={t.viewList} title={t.viewList} onClick={() => pick("list")}>
        <svg viewBox="0 0 24 24" aria-hidden><path d="M4 6h16M4 12h16M4 18h16" /></svg>
      </button>
      <button type="button" aria-pressed={view === "grid"} aria-label={t.viewGrid} title={t.viewGrid} onClick={() => pick("grid")}>
        <svg viewBox="0 0 24 24" aria-hidden><rect x="4" y="4" width="6.5" height="6.5" rx="1.5" /><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5" /><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5" /><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5" /></svg>
      </button>
    </div>
  );
}

function FacetIndex({ title, kind, path, logos, extra, toolbar }: {
  title: string; kind: "venues" | "artists"; path: (v: string) => string; logos?: boolean;
  extra?: Params; toolbar?: (first: FacetPage | undefined) => ReactNode; // the artists' filter and its query
}) {
  const { t } = useT();
  const web = useWeb();
  const [q, setQ] = useState("");
  const [typed, setTyped] = useState(""); // the box, ahead of the search while the user types
  const [area, setArea] = useState<string | null>(null);
  const [view, setView] = useState<View>(savedView);
  useTitle(title);
  useEffect(() => {
    const id = setTimeout(() => setQ(typed.trim()), web.search_debounce_ms);
    return () => clearTimeout(id);
  }, [typed, web.search_debounce_ms]);
  // The server searches, filters and pages (partylinks.facet_list): a page at a time, "load more" for the next.
  const qs = toQuery({ q: q || undefined, area: area ?? undefined, page_size: String(web.index_page_size), ...extra });
  const list = useInfiniteQuery({
    queryKey: ["facets", kind, qs],
    queryFn: ({ pageParam, signal }) => getJSON<FacetPage>(`/facets/${kind}?${qs}&page=${pageParam}`, signal),
    initialPageParam: 1,
    getNextPageParam: (last) => (last.page * web.index_page_size < last.total ? last.page + 1 : undefined),
    placeholderData: (prev) => prev, // the old list stays while the next search loads: no flash
    staleTime: 5 * 60_000,
  });
  const first = list.data?.pages[0];
  const shown = list.data?.pages.flatMap((p) => p.items) ?? [];
  const areas = first?.areas ?? [];
  const searching = typed.trim() !== q || (list.isFetching && !list.isFetchingNextPage);
  return (
    <>
      <Hero title={title}>
        <div className="facet-search">
          <svg viewBox="0 0 24 24" aria-hidden><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg>
          <input className="search" type="search" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={t.searchList} aria-label={t.searchList} enterKeyHint="search" />
          {searching && <span className="facet-spin" aria-hidden />}
        </div>
      </Hero>
      <main className="wrap">
        {toolbar?.(first)}
        {areas.length > 0 && (
          <div className="filters" role="group" aria-label={t.area}>
            <div className="chips scroll">
              <Chip on={!area} onClick={() => setArea(null)}>📍 {t.allAreas}</Chip>
              {areas.map((a) => (
                <Chip key={a} on={area === a} onClick={() => setArea(area === a ? null : a)}>{label(t.areas, a)}</Chip>
              ))}
            </div>
          </div>
        )}
        {first && (
          <div className="facet-bar">
            {/* the artist filter prints its own count */}
            {(!toolbar || q) && <span className="facet-count" aria-live="polite">{t.listCount(first.total)}</span>}
            <ViewToggle view={view} set={setView} />
          </div>
        )}
        {!first && (list.isError ? <p className="msg">{t.empty}</p> : <Loader />)}
        {first && !shown.length && <p className="msg">{q ? t.noMatch(q) : t.empty}</p>}
        {first?.fuzzy && <p className="msg">{t.closest(q)}</p>}
        <div className={"facets " + view + (searching ? " stale" : "")}>
          {shown.map((f) => (
            <Link key={f.value} className="fi" to={path(f.value)}>
              <Img src={f.image_url} alt="" text={initial(f.value)} className={"fi-av" + (logos && f.verified ? " logo" : "")} />
              <b><bdi>{f.value}</bdi>{f.verified && <Verified />}</b>
              <small>
                <span className="n">{t.events(f.events)}</span>
                {f.area ? ` · ${label(t.areas, f.area)}` : ""}{f.kind ? ` · ${f.kind.toUpperCase()}` : ""}
              </small>
              <svg className="fi-go" viewBox="0 0 24 24" aria-hidden><path d="M9 6l6 6-6 6" /></svg>
            </Link>
          ))}
        </div>
        {list.hasNextPage && (
          <div className="more">
            <button onClick={() => list.fetchNextPage()} disabled={list.isFetchingNextPage}>
              {list.isFetchingNextPage ? <span className="facet-spin" aria-hidden /> : t.loadMore}
            </button>
            {first && <small className="facet-count">{t.listShown(shown.length, first.total)}</small>}
          </div>
        )}
      </main>
    </>
  );
}

export function VenuesIndex() {
  const { t } = useT();
  return <FacetIndex title={t.venuesTitle} kind="venues" path={venuePath} logos />;
}

export function ArtistsIndex() {
  const { t } = useT();
  const { query } = useArtistFilter(undefined, undefined);
  return (
    <FacetIndex title={t.artistsTitle} kind="artists" path={artistPath} extra={query}
      toolbar={(first) => <ArtistFilterRows chips={first?.chips} total={first?.total} />} />
  );
}

export function VenuePage() {
  const { name = "" } = useParams();
  const { t, lang } = useT();
  useTitle(name);
  const about = useLandingInfo(useLocation().pathname, lang).data?.intro; // the venue's own text (venue_profiles)
  // Same query cache as the venues index: no extra request.
  const profile = useCategories({ anywhere: "true" }).data?.venues.find((f) => f.verified && f.value.toLowerCase() === name.toLowerCase());
  // Shares the query cache with the grid below, so this costs no extra request.
  const first = useEvents({ venue: name }).data?.pages[0]?.items[0];
  const where = first && [first.address, first.city, countryName(first.country, lang)].filter(Boolean).join(" · ");
  return (
    <>
      <Hero title={name}>
        {profile?.image_url && <Img src={profile.image_url} alt={name} className="venue-logo" eager />}
        {where && <p className="sub">📍 {where}</p>}
        {first && <MapLinks className="sub" place={{ ...first, venue_name: name }} label={t.map} />}
      </Hero>
      <main className="wrap">
        {(about || profile?.links?.length) && (
          <section className="venue-about">
            <h4 className="section-title">{t.aboutVenue}</h4>
            <SocialLinks urls={profile?.links ?? []} />
            {about && <About text={about} />}
          </section>
        )}
        <h4 className="section-title">{t.upcomingAt}</h4>
        <ScopedEvents key={name} base={{ venue: name }} />
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
  const { t, lang } = useT();
  useTitle(name);
  const info = useLandingInfo(useLocation().pathname, lang).data;
  const about = info?.intro; // written from our listings (artist_bio.py)
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
        {/* Spotify, YouTube, Instagram... merged from the ticket sites' profile and MusicBrainz (artist_links) */}
        <ArtistLinks links={info?.artist_links} name={profile?.value ?? name} />
        {profile?.profile_url && /^https?:\/\//i.test(profile.profile_url) && (
          <a className="sub" href={profile.profile_url} target="_blank" rel="noopener noreferrer">{siteName(profile.profile_url)} ↗</a>
        )}
      </Hero>
      <main className="wrap">
        {about && <p className="landing-intro">{about}</p>}
        <ArtistPlayer name={profile?.value ?? name} links={info?.artist_links} />
        <h4 className="section-title">{t.upcomingAt}</h4>
        <ScopedEvents key={name} base={{ artist: name }} />
        <SimilarArtists name={profile?.value ?? name} />
      </main>
    </>
  );
}

/** A venue's own text, four lines until opened (some sites write a page of history). */
function About({ text }: { text: string }) {
  const { t } = useT();
  const [open, setOpen] = useState(false);
  const long = text.length > 320;
  return (
    <>
      <p className={"landing-intro venue-text" + (long && !open ? " clamped" : "")} dir="auto">{text}</p>
      {long && !open && <button className="link-btn" onClick={() => setOpen(true)}>{t.readMore}</button>}
    </>
  );
}

/** Round icon links to a venue's own pages (Facebook, Instagram, its site). */
function SocialLinks({ urls }: { urls: string[] }) {
  const links = urls.filter((u) => /^https?:\/\//i.test(u));
  if (!links.length) return null;
  return (
    <div className="share">
      {links.map((u) => {
        const site = siteName(u);
        const icon = site === "facebook" || site === "instagram" ? site : "web";
        return (
          <a key={u} className={"share-btn " + icon} href={u} target="_blank" rel="noopener noreferrer" aria-label={site} title={site}>
            <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden>{SHARE_ICONS[icon]}</svg>
          </a>
        );
      })}
    </div>
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
