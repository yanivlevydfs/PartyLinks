// "Hear the lineup": a SoundCloud / Spotify / YouTube player for the lineup's artists, from their artist_links.
// Nothing third-party loads until a name is clicked (privacy, page weight); one player at a time.
import { useState } from "react";
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

/** Names with a playable link as buttons; the clicked one's player below. Nothing when no name has one. */
export function ArtistPlayer({ names, links }: { names: string[]; links?: Record<string, ArtistLink[]> }) {
  const t = useArtistLinksText();
  const [open, setOpen] = useState<string | null>(null);
  const playable = names.map((n) => [n, embedOf(links?.[n])] as const).filter((p): p is [string, Embed] => !!p[1]);
  if (!playable.length) return null;
  const current = playable.find(([n]) => n === open)?.[1];
  return (
    <section className="artist-player">
      <h4>🎧 {t.listen}</h4>
      <div className="ap-names">
        {playable.map(([n, e]) => (
          <button key={n} type="button" className={"ap-name al-" + e.service + (n === open ? " on" : "")} aria-pressed={n === open}
            onClick={() => setOpen(n === open ? null : n)}>
            {n === open ? "■" : "▶"} {n} <small>{t.services[e.service]}</small>
          </button>
        ))}
      </div>
      {current && (
        <iframe key={current.src} className="ap-frame" src={current.src} height={current.height} title={t.on(open!, t.services[current.service])}
          allow="autoplay; encrypted-media; clipboard-write; picture-in-picture" loading="lazy" />
      )}
    </section>
  );
}
