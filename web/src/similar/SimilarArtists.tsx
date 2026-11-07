// "If you liked X": artists like this one with upcoming shows (GET /artists/similar, partylinks.similar),
// from shared lineups, genres and venues in our listings. On the artist page.
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import type { Lang } from "../i18n";
import { artistPath, useT } from "../ui";

const TEXT: Record<Lang, { title: (name: string) => string; events: (n: number) => string }> = {
  he: { title: (name) => `אהבתם את ${name}? אולי גם`, events: (n) => `${n} הופעות` },
  en: { title: (name) => `Liked ${name}? Try`, events: (n) => `${n} shows` },
};

export function SimilarArtists({ name }: { name: string }) {
  const { lang } = useT();
  const p = TEXT[lang];
  const { data } = useQuery({
    queryKey: ["similar", name],
    queryFn: async () => {
      const res = await fetch(`/artists/similar?name=${encodeURIComponent(name)}`);
      if (!res.ok) throw new Error(`${res.status} similar`);
      return (await res.json()) as { artists: { name: string; events: number }[] };
    },
    staleTime: 10 * 60_000,
    enabled: !!name,
  });
  if (!data?.artists.length) return null;
  return (
    <section className="similar">
      <h4 className="section-title">{p.title(name)}</h4>
      <div className="map-cats">
        {data.artists.map((a) => (
          <Link key={a.name} className="map-cat" to={artistPath(a.name)}>
            <span aria-hidden>🎧</span><b dir="auto">{a.name}</b><small>{p.events(a.events)}</small>
          </Link>
        ))}
      </div>
    </section>
  );
}
