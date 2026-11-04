// Artist-link labels (aria / tooltip), Hebrew and English (kept beside the feature, not in the site-wide i18n).
import { useT } from "../ui";

const services = {
  spotify: "Spotify", youtube: "YouTube", apple_music: "Apple Music", soundcloud: "SoundCloud", deezer: "Deezer",
  bandcamp: "Bandcamp", instagram: "Instagram", tiktok: "TikTok", facebook: "Facebook", x: "X",
};

const he = {
  services: { ...services, website: "האתר הרשמי" } as Record<string, string>,
  on: (artist: string, service: string) => `${artist} ב-${service}`,
  listen: "האזינו",
};

const en: typeof he = {
  services: { ...services, website: "Official website" },
  on: (artist, service) => `${artist} on ${service}`,
  listen: "Listen",
};

export function useArtistLinksText() {
  return useT().lang === "en" ? en : he;
}
