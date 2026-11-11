// "Open in maps" + Waze links for a place. Searches by the place's own text (venue, street, city):
// stored coordinates are often a geocoder's guess (a city centre, a namesake street elsewhere), the
// text is what the site printed. Coordinates only when there is no text at all.
import { CITIES } from "../i18n";

type Place = { venue_name?: string | null; address?: string | null; city?: string | null; latitude?: number | null; longitude?: number | null };

export function mapQuery(p: Place): string {
  const parts: string[] = [];
  // The address may already name the city in the other language ("Tel Aviv-Yafo" vs our "תל אביב"):
  // drop the city only then. Any doubt keeps it, a repeated city never misplaces a pin, a missing one can.
  const city = p.city && p.address && cityNames(p.city).some((n) => norm(p.address!).includes(n)) ? null : p.city;
  for (const raw of [p.venue_name, p.address, city]) {
    const s = raw?.trim();
    // "Ben Gurion 44, Herzliya" + city "Herzliya": don't repeat what an earlier part already says.
    if (s && !parts.some((x) => x.toLowerCase().includes(s.toLowerCase()))) parts.push(s);
  }
  if (parts.length) return parts.join(" ");
  return p.latitude != null && p.longitude != null ? `${p.latitude},${p.longitude}` : "";
}

// "Be'er Sheva" / "Beer-Sheva" / "באר שבע" compare equal: letters and digits only, lower case.
const norm = (s: string) => s.toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");

function cityNames(city: string): string[] {
  const c = norm(city);
  const known = Object.values(CITIES).find((x) => norm(x.he) === c || norm(x.en) === c);
  return (known ? [known.he, known.en] : [city]).map(norm);
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
  console.assert(mapQuery({ venue_name: "רידינג3", address: "HaTa'arucha St 3, Tel Aviv-Yafo, Israel", city: "תל אביב" }) === "רידינג3 HaTa'arucha St 3, Tel Aviv-Yafo, Israel");
  console.assert(mapQuery({ venue_name: "בית ציוני אמריקה", city: "תל אביב" }) === "בית ציוני אמריקה תל אביב");
  // A comma that isn't a city keeps the city.
  console.assert(mapQuery({ address: "דיזנגוף 50, קומה 2", city: "תל אביב" }) === "דיזנגוף 50, קומה 2 תל אביב");
  // Same city, other address, still named: "רמת גן" must not vanish because the address says "Tel Aviv".
  console.assert(mapQuery({ address: "Jabotinsky 1, Tel Aviv", city: "רמת גן" }) === "Jabotinsky 1, Tel Aviv רמת גן");
  console.assert(mapQuery({ latitude: 32.1, longitude: 34.8 }) === "32.1,34.8");
}
