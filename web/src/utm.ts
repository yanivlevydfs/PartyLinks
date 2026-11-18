// UTM tags on every link that leaves the site for a content provider (a ticket site's home page, a box
// office, a venue's / artist's / restaurant's own pages, links inside an event's description), so their
// analytics (GA4 and others) see the visits came from us. Ticket links are tagged on the server instead,
// in the /go/ redirect (src/partylinks/utm.py), with the same utm_source. Tools we only hand off to (maps,
// Waze, calendars, share buttons, WhatsApp, Telegram) are left alone: tags there break or mean nothing.

const SOURCE = "spotx"; // = utm.source in config/settings.yaml
const MEDIUM = "referral";

/** ``url`` with utm_source / utm_medium / utm_campaign (our page) / utm_content (which link). Any utm_* the
 * URL already carries is replaced; every other parameter is kept byte for byte, as on the server. Links
 * that are not http(s) or point back at our own site come back unchanged. */
export function outbound(url: string, campaign: string, content?: string): string {
  if (!/^https?:\/\//i.test(url)) return url;
  try {
    if (new URL(url).origin === window.location.origin) return url;
  } catch {
    return url;
  }
  const hashAt = url.indexOf("#");
  const hash = hashAt < 0 ? "" : url.slice(hashAt);
  const rest = hashAt < 0 ? url : url.slice(0, hashAt);
  const queryAt = rest.indexOf("?");
  const base = queryAt < 0 ? rest : rest.slice(0, queryAt);
  const kept = (queryAt < 0 ? "" : rest.slice(queryAt + 1)).split("&").filter((p) => p && !p.toLowerCase().startsWith("utm_"));
  const tags = new URLSearchParams({ utm_source: SOURCE, utm_medium: MEDIUM, utm_campaign: campaign });
  if (content) tags.set("utm_content", content);
  return `${base}?${[...kept, tags.toString()].join("&")}${hash}`;
}
