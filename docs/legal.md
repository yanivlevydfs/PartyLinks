# Legal and privacy

| Page | Basis | Source |
| --- | --- | --- |
| `/accessibility` | Accessibility statement, Israeli standard IS 5568 | `site.accessibility` in settings |
| `/privacy` | Privacy and cookie policy, Privacy Protection Law (Amendment 13) | `site.privacy` in settings, and the storage table in `web/src/consent.tsx` |
| `/terms` | Terms of use: SPOTX only aggregates listings sold on other sites | `web/src/pages/Terms.tsx` |

## Cookies and tracking

- **No cookies for ads or analytics.** The site sets none.
- **Necessary browser storage** (settings, consent choice) needs no consent, but it is listed on
  the privacy page.
- **The only optional category is external content:** OpenFreeMap map tiles
  (`tiles.openfreemap.org`, OpenStreetMap data), which load only after consent.
- **Fonts are self-hosted.**

The cookie banner and the list of everything stored on the device live in `web/src/consent.tsx`.
Keep that list in sync with the code.

## Server-side records with no personal data

- **Ticket-link clicks** store no IP address, cookie or user agent (see [ticket links](ticket-links.md)).
- **UTM tags** go to the seller in the link itself; we set nothing in the visitor's browser.

Adding client-side analytics later would mean updating `consent.tsx` and the privacy page.

## Open items

- [x] **Privacy contact email:** `site.privacy.email` is spotxisrael@gmail.com (also the contact address).
- [ ] **Accessibility coordinator:** `site.accessibility.email` is spotxisrael@gmail.com; the coordinator's name is still empty.
  The law requires a reachable coordinator.
- [ ] Have a lawyer review the privacy policy text.
