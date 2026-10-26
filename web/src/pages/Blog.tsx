// Magazine: guides that rank in search, with live event lists inside the articles.
// The server renders the same URLs (title, BlogPosting / FAQ JSON-LD) for crawlers.
import type { MouseEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useEvents, usePost, usePosts } from "../api";
import type { PostBlock, PostSummary } from "../types";
import { Card, Hero, Img, Loader, useT, useTitle } from "../ui";
import NotFound from "./NotFound";

function PostCard({ p, featured }: { p: PostSummary; featured?: boolean }) {
  const { t, lang } = useT();
  return (
    <Link className={"post-card" + (featured ? " featured" : "")} to={`/blog/${p.slug}`}>
      <Img src={p.image} alt="" className="post-img" />
      <div className="post-card-body" lang={p.lang} dir={p.lang === "he" ? "rtl" : "ltr"}>
        <span className="post-cat">{p.category[lang]}</span>
        <h3>{p.title}</h3>
        <p>{p.description}</p>
        <small>{new Date(p.date).toLocaleDateString(t.locale)} · {t.blog.minutes(p.read_minutes)}</small>
      </div>
    </Link>
  );
}

export function BlogIndex() {
  const { category } = useParams();
  const { t, lang } = useT();
  const all = usePosts(lang).data; // categories (with counts) come from the unfiltered list
  const q = usePosts(lang, category);
  const name = all?.categories.find((c) => c.key === category)?.[lang];
  useTitle(name ? `${name} · ${t.blog.title}` : t.blog.title);
  const [first, ...rest] = q.data?.items ?? [];
  return (
    <>
      <Hero title={name ?? t.blog.title}>
        <p className="sub">{t.blog.intro}</p>
      </Hero>
      <main className="wrap">
        <nav className="chips filters" aria-label={t.nav.blog}>
          <Link className={"chip" + (!category ? " on" : "")} to="/blog">{t.blog.all}</Link>
          {all?.categories.map((c) => (
            <Link key={c.key} className={"chip" + (c.key === category ? " on" : "")} to={`/blog/category/${c.key}`}>
              {c[lang]} <small>{c.posts}</small>
            </Link>
          ))}
        </nav>
        {q.isPending && <Loader />}
        {q.isError && <p className="msg">{t.error}</p>}
        {q.data?.items.length === 0 && <p className="msg">{t.blog.empty}</p>}
        {first && <PostCard p={first} featured />}
        <div className="post-grid">{rest.map((p) => <PostCard key={p.slug} p={p} />)}</div>
      </main>
    </>
  );
}

/** A live list inside an article; hidden when nothing matches right now. */
function LiveEvents({ params }: { params: Record<string, string> }) {
  const items = useEvents(params).data?.pages[0]?.items ?? [];
  if (items.length === 0) return null;
  return <div className="grid post-events">{items.map((e) => <Card key={e.id} e={e} />)}</div>;
}

function Block({ b }: { b: PostBlock }) {
  return "events" in b ? <LiveEvents params={b.events} /> : <div dangerouslySetInnerHTML={{ __html: b.html }} />;
}

export function BlogPost() {
  const { slug = "" } = useParams();
  const { t, lang } = useT();
  const navigate = useNavigate();
  const q = usePost(slug, lang);
  const p = q.data;
  const dir = p?.lang === "he" ? "rtl" : "ltr";
  useTitle(p?.title);

  // Links inside the article HTML are plain <a>: keep internal ones in the app.
  const onClick = (e: MouseEvent) => {
    const a = (e.target as HTMLElement).closest("a");
    const href = a?.getAttribute("href");
    // "//host" and "/\host" leave the site: react-router 6 would follow them (GHSA-wrjc-x8rr-h8h6).
    if (href && /^\/(?![/\\])/.test(href) && !e.metaKey && !e.ctrlKey) {
      e.preventDefault();
      navigate(href);
    }
  };

  if (q.isError) return <NotFound />;
  if (!p) return <Loader />;
  return (
    <>
      <Hero title={p.title}>
        <p className="sub">
          <Link to={`/blog/category/${p.category.key}`}>{p.category[lang]}</Link> ·{" "}
          <time dateTime={p.date}>{new Date(p.date).toLocaleDateString(t.locale)}</time> · {t.blog.minutes(p.read_minutes)}
        </p>
      </Hero>
      <main className="wrap post-layout">
        {p.toc.length > 1 && (
          <nav className="post-toc" aria-label={t.blog.toc}>
            <b>{t.blog.toc}</b>
            <ol lang={p.lang} dir={dir}>{p.toc.map((h) => <li key={h.id}><a href={`#${h.id}`}>{h.title}</a></li>)}</ol>
          </nav>
        )}
        <article className="post" lang={p.lang} dir={dir} onClick={onClick}>
          <p className="post-lead">{p.description}</p>
          {p.blocks.map((b, i) => <Block key={i} b={b} />)}
          {p.faq.length > 0 && (
            <section className="post-faq">
              <h2>{t.blog.faq}</h2>
              {p.faq.map((f) => (
                <details key={f.q}>
                  <summary>{f.q}</summary>
                  <p>{f.a}</p>
                </details>
              ))}
            </section>
          )}
        </article>
      </main>
      {p.related.length > 0 && (
        <section className="wrap">
          <h4 className="section-title">{t.blog.related}</h4>
          <div className="post-grid">{p.related.map((r) => <PostCard key={r.slug} p={r} />)}</div>
        </section>
      )}
    </>
  );
}
