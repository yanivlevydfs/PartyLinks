// A friend answered while the page is open: say so at once, since the list sits below the form and a quiet
// change there goes unseen. Compares each refresh with the last one; the first load is never news.
import { useEffect, useRef, useState } from "react";
import { usePlanText } from "./i18n";
import type { Vote } from "./types";

const sig = (v: Vote) => `${v.answer}|${v.ride}|${v.picks.join()}`;

export function useNewVotes(votes: Vote[] | undefined): Vote[] {
  const seen = useRef<Map<string, string> | null>(null);
  const [news, setNews] = useState<Vote[]>([]);
  useEffect(() => {
    if (!votes) return;
    const before = seen.current;
    seen.current = new Map(votes.filter((v) => !v.mine).map((v) => [v.name, sig(v)]));
    if (!before) return;
    const changed = votes.filter((v) => !v.mine && before.get(v.name) !== sig(v));
    if (changed.length) setNews(changed);
  }, [votes]);
  useEffect(() => {
    if (!news.length) return;
    const hide = setTimeout(() => setNews([]), 8000);
    return () => clearTimeout(hide);
  }, [news]);
  return news;
}

export function NewVotesToast({ news, listId }: { news: Vote[]; listId: string }) {
  const p = usePlanText();
  if (!news.length) return null;
  const show = () => document.getElementById(listId)?.scrollIntoView({ behavior: "smooth", block: "center" });
  return (
    <button type="button" className="plan-toast" role="status" onClick={show}>
      {news.slice(0, 3).map((v) => (
        <span key={v.name} dir="auto">🔔 {v.name}: {p[v.answer]}</span>
      ))}
    </button>
  );
}
