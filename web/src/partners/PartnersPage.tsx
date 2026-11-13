// /partners: for promoters, venues and brands. Texts in texts.json, which the server's crawler page
// (src/partylinks/partners_page.py) renders too, so both always say the same.
import { Link } from "react-router-dom";
import { INFO_LINKS, Mail } from "../pages/Info";
import { Hero, useT, useTitle } from "../ui";
import TEXTS from "./texts.json";
import "./partners.css";

type Section = {
  id: string;
  h: string;
  p?: string;
  stats?: string[][]; // [value, label]
  note?: string; // after the cards
  list?: string[];
  steps?: string[];
  cards?: string[][]; // [title, tag, text]
  faq?: string[][]; // [question, answer]
  link?: string[]; // [path, label]
};

export function PartnersPage() {
  const { lang } = useT();
  const t = TEXTS[lang];
  useTitle(t.metaTitle);
  return (
    <>
      <Hero title={INFO_LINKS.find((l) => l.path === "/partners")!.label[lang]}>
        <p className="pt-eyebrow">{t.eyebrow}</p>
        <p className="hero-pitch">{t.lead}</p>
        <p className="pt-ctas">
          <a className="c-btn" href="#contact">{t.cta}</a>
          <a className="c-btn ghost" href="#free">{t.ctaFree}</a>
        </p>
      </Hero>
      <main className="wrap statement pt">
        {(t.sections as Section[]).map((s) => (
          <section key={s.id} id={s.id} aria-labelledby={`${s.id}-h`}>
            <h2 id={`${s.id}-h`}>{s.h}</h2>
            {s.p && <p>{s.p}</p>}
            {s.stats && (
              <dl className="pt-stats">
                {s.stats.map(([v, label]) => (
                  <div key={label}><dt>{label}</dt><dd><span dir="ltr">{v}</span></dd></div>
                ))}
              </dl>
            )}
            {s.list && <ul>{s.list.map((x) => <li key={x}>{x}</li>)}</ul>}
            {s.steps && <ol className="pt-steps">{s.steps.map((x) => <li key={x}>{x}</li>)}</ol>}
            {s.cards && (
              <ul className="pt-cards">
                {s.cards.map(([h, tag, p]) => (
                  <li key={h}>
                    <h3>{h}</h3>
                    <span className="pt-tag">{tag}</span>
                    <p>{p}</p>
                  </li>
                ))}
              </ul>
            )}
            {s.note && <p>{s.note}</p>}
            {s.faq?.map(([q, a]) => (
              <details key={q} className="pt-faq">
                <summary>{q}</summary>
                <p>{a}</p>
              </details>
            ))}
            {s.id === "contact" && <Mail subject={t.subject} />}
            {s.link && <p><Link to={s.link[0]}>{s.link[1]}</Link></p>}
          </section>
        ))}
      </main>
    </>
  );
}
