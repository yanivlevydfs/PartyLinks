// A home row's card; for an admin, a pinned one also gets ✏️ edit (the picker) and ✕ unpin, right on the row.
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useMe } from "../accounts/useMe";
import type { EventSummary } from "../types";
import { Card } from "../ui";
import { usePinChange } from "./api";
import { usePinText } from "./i18n";
import { PinSheet } from "./PinButtons";

export function RailCard({ e }: { e: EventSummary }) {
  const admin = useMe().data?.role === "admin";
  return admin && e.pinned ? <PinnedCard e={e} /> : <Card e={e} />;
}

function PinnedCard({ e }: { e: EventSummary }) {
  const { p } = usePinText();
  const { unpin } = usePinChange();
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [gone, setGone] = useState(false);
  if (gone) return null; // unpinned: off this row at once; the rows refresh within a minute
  const remove = () =>
    confirm(p.unpinAsk) &&
    unpin.mutate(e.id, { onSuccess: () => { setGone(true); qc.invalidateQueries({ queryKey: ["rails"] }); } });
  return (
    <div className="card-wrap pin-card">
      <Card e={e} />
      <div className="pin-tools" aria-label={p.tab}>
        <span className="pin-flag">📌</span>
        <button type="button" onClick={() => setEditing(true)}>✏️ {p.edit}</button>
        <button type="button" onClick={remove} disabled={unpin.isPending}>✕ {p.unpinShort}</button>
        {unpin.isError && <span className="pin-err">{p.error}</span>}
      </div>
      {editing && <PinSheet event={e} onClose={() => setEditing(false)} />}
    </div>
  );
}
