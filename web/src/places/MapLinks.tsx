// "Open in maps" + Waze links for a place. Searches by the place's own text (venue, street, city):
// stored coordinates are often a geocoder's guess (a city centre, a namesake street elsewhere), the
// text is what the site printed. Coordinates only when there is no text at all.
type Place = { venue_name?: string | null; address?: string | null; city?: string | null; latitude?: number | null; longitude?: number | null };

export function mapQuery(p: Place): string {
  const parts: string[] = [];
  for (const raw of [p.venue_name, p.address, p.city]) {
    const s = raw?.trim();
    // "Ben Gurion 44, Herzliya" + city "Herzliya": don't repeat what an earlier part already says.
    if (s && !parts.some((x) => x.toLowerCase().includes(s.toLowerCase()))) parts.push(s);
  }
  if (parts.length) return parts.join(" ");
  return p.latitude != null && p.longitude != null ? `${p.latitude},${p.longitude}` : "";
}

export function MapLinks({ place, label, className }: { place: Place; label: string; className?: string }) {
  const q = mapQuery(place);
  if (!q) return null;
  const enc = encodeURIComponent(q);
  return (
    <>
      <a className={className} href={`https://www.google.com/maps/search/?api=1&query=${enc}`} target="_blank" rel="noopener noreferrer">
        {label} ↗
      </a>{" "}
      <a className={className} href={`https://waze.com/ul?q=${enc}&navigate=yes`} target="_blank" rel="noopener noreferrer">
        Waze ↗
      </a>
    </>
  );
}

if (import.meta.env?.DEV) {
  console.assert(mapQuery({ venue_name: "בארבי", address: "בן גוריון 44, הרצליה", city: "הרצליה", latitude: 32, longitude: 34 }) === "בארבי בן גוריון 44, הרצליה");
  console.assert(mapQuery({ latitude: 32.1, longitude: 34.8 }) === "32.1,34.8");
}
