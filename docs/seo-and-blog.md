# SEO, social previews, AI visibility and the blog

The website is a React app, but crawlers, link previews and LLM fetchers often don't run
JavaScript. So `seo.py` serves every public page from FastAPI with its own `<head>` and a
plain-HTML copy of the content inside `#root`. React replaces that copy on load.

## Server-rendered pages

`/`, `/event/<slug>`, `/venue/<name>`, `/venues`, `/artist/<name>`, `/artists`, `/map`, `/abroad`,
`/tonight`, `/weekend`, `/genre/<genre>`, `/genre/<genre>/<when>`, `/city/<city>`,
`/accessibility`, `/privacy`, `/terms`, and the blog pages.

Each page has:

- **Head tags:** `<title>`, description, canonical URL, and hreflang for Hebrew and English
  (`?lang=en`).
- **Social previews:** Open Graph and Twitter card tags (the event artwork, or `og-default.png`)
  and local geo tags.
- **Structured data:** schema.org JSON-LD: `MusicEvent` / `Event` with offers, place, performers
  and Israel-time dates, plus `BreadcrumbList`, `ItemList`, `WebSite`, `Organization`,
  `MusicVenue` and `Person`.

Unknown pages return a real `404`. Filters on the site move the URL to their landing page
(`/genre/...`, `/city/...`), so filtered views are indexable.

## Crawl files

| File | Contents |
| --- | --- |
| `/sitemap.xml` | A sitemap index, with parts of up to 10,000 URLs at `/sitemap-<part>.xml`: upcoming events, venues, artists, landing pages and posts, with hreflang alternates |
| `/robots.txt` | Search engines and AI crawlers allowed. Private paths disallowed (`/admin`, `/go/`, `/health`, `/runs`, `/sources`, `/changes`, API docs). Data harvesters disallowed and also refused by the server |
| `/llms.txt` | A site summary, the API, and the next 400 events |

Set `site.base_url` to the public domain: canonical links, the sitemap, JSON-LD and share
previews use it. Favicons and the web manifest are in `web/public/`.

## Blog (`blog.py`)

Evergreen guides that rank for what people search ("מסיבות חנוכה", "חיי לילה בתל אביב") and stay
fresh with live event lists.

Each post is a file `content/blog/<slug>.html`: a YAML header between `---` lines, then an HTML body.

- **Live event lists.** A comment such as
  `<!--events genre=techno when=weekend city=tel-aviv limit=6-->` in the body is replaced by
  matching upcoming events. `venue=`, `artist=` and `free=true` also work.
- **Scheduled posts.** A post dated in the future, or marked `draft: true`, stays hidden until its
  date.
- **English version.** Every post also gets `content/blog/en/<slug>.html`: the same shape, with
  only `title`, `description` and `faq` in the header (date, category and the rest come from the
  Hebrew file) and the same `<!--events-->` comments. `?lang=en` serves it, with hreflang and
  sitemap alternates. A post without one is shown in Hebrew to English readers too.

Posts are served three ways:

- **For crawlers:** server-rendered pages (`/blog`, `/blog/category/<key>`, `/blog/<slug>`).
- **For the React site:** JSON (`/posts`, `/posts/<slug>`).
- **For feed readers:** RSS (`/blog/rss.xml`).
