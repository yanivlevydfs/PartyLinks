// A home row's card; for an admin, every card gets its tools right on the row: a pinned one ✏️ edit (the picker)
// and ✕ unpin, any other 📌 pin (the picker, this row checked).
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useMe } from "../accounts/useMe";
import type { EventSummary } from "../types";
import { Card } from "../ui";
import { usePinChange } from "./api";
import { usePinText } from "./i18n";
import { PinSheet } from "./PinButtons";

export function RailCard({ e, rail }: { e: EventSummary; rail?: string }) {
  return useMe().data?.role === "admin" ? <AdminCard e={e} rail={rail} /> : <Card e={e} />;
}

function AdminCard({ e, rail }: { e: EventSummary; rail?: string }) {
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
        {e.pinned ? (
          <>
            <span className="pin-flag">📌</span>
            <button type="button" onClick={() => setEditing(true)}>✏️ {p.edit}</button>
            <button type="button" onClick={remove} disabled={unpin.isPending}>✕ {p.unpinShort}</button>
          </>
        ) : <button type="button" onClick={() => setEditing(true)}>{p.pinBtn}</button>}
        {unpin.isError && <span className="pin-err">{p.error}</span>}
      </div>
      {editing && <PinSheet event={e} rail={rail} onClose={() => setEditing(false)} />}
    </div>
  );
}
