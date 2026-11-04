// An artist's music / social links (Spotify, YouTube, Instagram...) as round icons. They come with the
// event (lineup_links) or the artist page's /landing-info: no request of their own.
import type { ReactNode } from "react";
import { SHARE_ICONS } from "../ui";
import { useArtistLinksText } from "./i18n";
import "./artist_links.css";

export interface ArtistLink { service: string; url: string }

const ICONS: Record<string, ReactNode> = {
  ...SHARE_ICONS,
  website: SHARE_ICONS.web,
  spotify: (
    <g fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <circle cx="12" cy="12" r="9.5" /><path d="M7 9.3c3.4-1 7.2-.7 10 .9M7.6 12.4c2.8-.8 5.8-.5 8.2.8M8.2 15.3c2.2-.6 4.4-.4 6.3.6" />
    </g>
  ),
  youtube: (
    <>
      <path d="M21.6 7.2a2.6 2.6 0 0 0-1.8-1.8C18.2 5 12 5 12 5s-6.2 0-7.8.4A2.6 2.6 0 0 0 2.4 7.2 27 27 0 0 0 2 12a27 27 0 0 0 .4 4.8 2.6 2.6 0 0 0 1.8 1.8C5.8 19 12 19 12 19s6.2 0 7.8-.4a2.6 2.6 0 0 0 1.8-1.8A27 27 0 0 0 22 12a27 27 0 0 0-.4-4.8z" />
      <path d="m10 15 5.2-3L10 9z" fill="var(--panel-solid, #111)" />
    </>
  ),
  apple_music: <path d="M18 3v12.2a2.8 2.8 0 1 1-1.8-2.6V7.4l-7.4 1.7v8.1A2.8 2.8 0 1 1 7 14.6V5.6z" />,
  soundcloud: (
    <path d="M10 8.2c.6-.3 1.4-.5 2.2-.5a5 5 0 0 1 5 4.3 3 3 0 1 1 .8 5.9H10zM8.2 9.2h1v8.7h-1zM6.4 10.2h1v7.7h-1zM4.6 11.4h1v6.5h-1zM2.8 13h1v4.9h-1z" />
  ),
  deezer: <path d="M2 16h4v2H2zM7 16h4v2H7zM12 16h4v2h-4zM17 16h5v2h-5zM12 12.5h4v2h-4zM17 12.5h5v2h-5zM17 9h5v2h-5zM7 12.5h4v2H7zM17 5.5h5v2h-5z" />,
  bandcamp: <path d="M2 18 8.5 6H22l-6.5 12z" />,
  tiktok: <path d="M14 3h3a4 4 0 0 0 4 4v3a7.2 7.2 0 0 1-4-1.2V15a6 6 0 1 1-6-6v3.1a3 3 0 1 0 3 2.9z" />,
};

/** Icons for the links; nothing when there are none. ``small``: beside a name in a lineup. */
export function ArtistLinks({ links, name, small = false }: { links?: ArtistLink[]; name: string; small?: boolean }) {
  const t = useArtistLinksText();
  const shown = (links ?? []).filter((l) => ICONS[l.service] && /^https?:\/\//i.test(l.url));
  if (!shown.length) return null;
  return (
    <span className={"share artist-links" + (small ? " al-small" : "")}>
      {shown.map((l) => {
        const label = t.on(name, t.services[l.service] ?? l.service);
        return (
          <a key={l.service} className={"share-btn al-" + l.service} href={l.url} target="_blank" rel="noopener noreferrer" aria-label={label} title={label}>
            <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden>{ICONS[l.service]}</svg>
          </a>
        );
      })}
    </span>
  );
}
