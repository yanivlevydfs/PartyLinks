// Concerts and festivals abroad: their own feed, kept out of the Israeli one (API ?abroad=true).
// /abroad/<country>: one country (its code and title come from the server, like the page crawlers get).
import { useEffect, useState } from "react";
import { useLocation, useParams } from "react-router-dom";
import { useLandingInfo } from "../api";
import { EventGrid, Hero, Related, useT, useTitle } from "../ui";

export default function Abroad() {
  const { t, lang } = useT();
  const { country } = useParams();
  const { pathname } = useLocation();
  const info = useLandingInfo(pathname, lang).data;
  const title = (country && info?.title) || t.abroad.title;
  useTitle(title);
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const id = setTimeout(() => setQ(text.trim()), 300);
    return () => clearTimeout(id);
  }, [text]);
  return (
    <>
      <Hero title={title}>
        <p className="sub">{(country && info?.intro) || t.abroad.intro}</p>
        <input className="search" type="search" value={text} onChange={(e) => setText(e.target.value)} placeholder={t.search} aria-label={t.search} />
      </Hero>
      <main className="wrap">
        {(!country || info?.country) && (
          <EventGrid params={{ abroad: "true", country: info?.country ?? undefined, q: q.length >= 2 ? q : undefined }} />
        )}
        <Related path={pathname} />
      </main>
    </>
  );
}
