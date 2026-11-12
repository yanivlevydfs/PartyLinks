// The ticket sites on a card's picture: the first few names and a "+N" chip that opens the full list,
// so a show sold on twelve sites doesn't cover its picture (2026-11-13). How many names: GET /site
// (site.card_sources_shown, the admin's config tab).
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import "./card-sources.css";

const useShown = () =>
  useQuery({ queryKey: ["site"], queryFn: () => fetch("/site").then((r) => r.json() as Promise<{ card_sources_shown?: number }>) })
    .data?.card_sources_shown ?? 2;

export function CardSources({ names }: { names: string[] }) {
  const shown = useShown();
  const [open, setOpen] = useState(false);
  const rest = names.slice(shown);
  const stop = (ev: React.MouseEvent) => { ev.preventDefault(); ev.stopPropagation(); };
  return (
    <span className="sources on-img card-sources">
      {names.slice(0, shown).map((n) => <span key={n} className="src">{n}</span>)}
      {rest.length > 0 && (
        <button type="button" className="src more" title={rest.join(" · ")} aria-expanded={open}
          onClick={(ev) => { stop(ev); setOpen((o) => !o); }}>+{rest.length}</button>
      )}
      {open && (
        <span className="all" role="list" onClick={stop}>
          {names.map((n) => <span key={n} role="listitem">{n}</span>)}
        </span>
      )}
    </span>
  );
}
