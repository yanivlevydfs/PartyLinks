// Play an artist's SoundCloud / Spotify / YouTube in an embedded player, from their artist_links.
// Nothing third-party loads until a play button is clicked (privacy, page weight); one player at a time.
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { artistPath } from "../ui";
import type { ArtistLink } from "./ArtistLinks";
import { useArtistLinksText } from "./i18n";
import "./artist_links.css";

interface Embed { service: string; src: string; height: number }

// SoundCloud first: DJs post their sets there; Spotify plays top tracks; YouTube a channel's uploads.
function embedOf(links: ArtistLink[] = []): Embed | null {
  for (const service of ["soundcloud", "spotify", "youtube"]) {
    const url = links.find((l) => l.service === service)?.url;
    if (!url) continue;
    if (service === "soundcloud")
      return { service, height: 300, src: `https://w.soundcloud.com/player/?url=${encodeURIComponent(url)}&color=%23ff5500&auto_play=true&visual=true` };
    const spotify = url.match(/open\.spotify\.com\/(?:intl-\w+\/)?artist\/(\w+)/);
    if (spotify) return { service, height: 352, src: `https://open.spotify.com/embed/artist/${spotify[1]}?theme=0` };
    const channel = url.match(/youtube\.com\/channel\/UC([\w-]+)/); // its uploads playlist is UU + the rest
    if (channel) return { service, height: 300, src: `https://www.youtube-nocookie.com/embed/videoseries?list=UU${channel[1]}&autoplay=1` };
  }
  return null;
}

function PlayButton({ name, embed, on, toggle }: { name: string; embed: Embed; on: boolean; toggle: () => void }) {
  const t = useArtistLinksText();
  const label = t.on(name, t.services[embed.service]);
  return (
    <button type="button" className={"ap-play al-" + embed.service + (on ? " on" : "")} aria-pressed={on} aria-label={label} title={label} onClick={toggle}>
      {on ? "■" : "▶"}
    </button>
  );
}

function Frame({ name, embed }: { name: string; embed: Embed }) {
  const t = useArtistLinksText();
  return (
    <iframe key={embed.src} className="ap-frame" src={embed.src} height={embed.height} title={t.on(name, t.services[embed.service])}
      allow="autoplay; encrypted-media; clipboard-write; picture-in-picture" loading="lazy" />
  );
}

/** The event's lineup: each name links to its artist page, with a ▶ when it has something to play; the player opens below. */
export function LineupPlayer({ names, links, children }: { names: string[]; links?: Record<string, ArtistLink[]>; children?: ReactNode }) {
  const [open, setOpen] = useState<string | null>(null);
  const playing = open ? embedOf(links?.[open]) : null;
  return (
    <>
      <div className="lineup-list">
        {names.map((a) => {
          const embed = embedOf(links?.[a]);
          return (
            <span key={a} className="lineup-item">
              <Link to={artistPath(a)}>{a}</Link>
              {embed && <PlayButton name={a} embed={embed} on={a === open} toggle={() => setOpen(a === open ? null : a)} />}
            </span>
          );
        })}
        {children}
      </div>
      {open && playing && <Frame name={open} embed={playing} />}
    </>
  );
}

/** The artist page: one "▶ Listen on SoundCloud" button and its player. Nothing when there is nothing to play. */
export function ArtistPlayer({ name, links }: { name: string; links?: ArtistLink[] }) {
  const t = useArtistLinksText();
  const [on, setOn] = useState(false);
  const embed = embedOf(links);
  if (!embed) return null;
  return (
    <section className="artist-player">
      <button type="button" className={"ap-listen al-" + embed.service + (on ? " on" : "")} aria-pressed={on} onClick={() => setOn(!on)}>
        {on ? "■" : "▶"} {t.listen} · {t.services[embed.service]}
      </button>
      {on && <Frame name={name} embed={embed} />}
    </section>
  );
}
