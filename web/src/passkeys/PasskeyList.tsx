// /account: the devices that sign in with a fingerprint / face, "Add this device", and removing one.
import { useState } from "react";
import { useT } from "../ui";
import { supported, usePasskeyActions, usePasskeys } from "./passkeys";
import { usePasskeyText } from "./i18n";
import "./passkeys.css";

export function PasskeyList() {
  const t = usePasskeyText();
  const { t: ui } = useT();
  const list = usePasskeys();
  const { add, remove } = usePasskeyActions();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const date = (iso: string) => new Date(iso).toLocaleDateString(ui.locale, { day: "numeric", month: "short", year: "numeric" });

  const onAdd = async () => {
    setBusy(true);
    setNote("");
    const failed = await add();
    setBusy(false);
    setNote(failed === null ? t.added : failed === "cancelled" ? "" : failed === "already" ? t.already : t.failed);
  };
  const onRemove = async (id: number) => setNote((await remove(id)) ? "" : t.failed);

  return (
    <section className="acc-form pk-list">
      <h2>{t.title}</h2>
      <p className="muted">{t.intro}</p>
      {list.data && (list.data.length ? (
        <ul>
          {list.data.map((p) => (
            <li key={p.id}>
              <div>
                <b>{p.name}</b>
                <small className="muted">{t.created} {date(p.created_at)} · {t.lastUsed} {p.last_used_at ? date(p.last_used_at) : t.never}</small>
              </div>
              <button type="button" className="link-btn" onClick={() => onRemove(p.id)} aria-label={t.removeLabel(p.name)}>{t.remove}</button>
            </li>
          ))}
        </ul>
      ) : <p className="muted">{t.none}</p>)}
      {supported()
        ? <button type="button" className="c-btn" onClick={onAdd} disabled={busy} aria-busy={busy}>{busy ? t.working : t.add}</button>
        : <p className="muted">{t.unsupported}</p>}
      <p className="muted" role="status">{note}</p>
    </section>
  );
}
