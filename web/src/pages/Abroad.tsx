// Concerts and festivals abroad: their own feed, kept out of the Israeli one (API ?abroad=true).
import { useEffect, useState } from "react";
import { EventGrid, Hero, useT, useTitle } from "../ui";

export default function Abroad() {
  const { t } = useT();
  useTitle(t.abroad.title);
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    const id = setTimeout(() => setQ(text.trim()), 300);
    return () => clearTimeout(id);
  }, [text]);
  return (
    <>
      <Hero title={t.abroad.title}>
        <p className="sub">{t.abroad.intro}</p>
        <input className="search" type="search" value={text} onChange={(e) => setText(e.target.value)} placeholder={t.search} aria-label={t.search} />
      </Hero>
      <main className="wrap">
        <EventGrid params={{ abroad: "true", q: q.length >= 2 ? q : undefined }} />
      </main>
    </>
  );
}
