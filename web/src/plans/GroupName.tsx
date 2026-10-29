// The group's name above a plan: shown to everyone when set; its creator (signed in) names or renames it.
import { useState } from "react";
import { send } from "../accounts/useMe";
import { usePlanText } from "./i18n";
import type { Plan } from "./types";

export function GroupName({ plan, onSaved }: { plan: Plan; onSaved: (plan: Plan) => void }) {
  const p = usePlanText();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(plan.name ?? "");
  const [failed, setFailed] = useState(false);
  if (!plan.owner) return plan.name ? <p className="plan-name" dir="auto">👥 {plan.name}</p> : null;
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      onSaved((await send<Plan>(`/plans/${encodeURIComponent(plan.id)}`, "PATCH", { name: text.trim() }))!);
      setEditing(false);
      setFailed(false);
    } catch {
      setFailed(true);
    }
  };
  if (!editing) {
    return (
      <p className="plan-name" dir="auto">
        {plan.name ? `👥 ${plan.name}` : ""}
        <button type="button" className="link-btn" onClick={() => setEditing(true)}>{plan.name ? p.rename : p.nameGroup}</button>
      </p>
    );
  }
  return (
    <form className="plan-name" onSubmit={save}>
      <input className="search" value={text} maxLength={60} onChange={(e) => setText(e.target.value)} placeholder={p.groupPlaceholder} aria-label={p.nameGroup} dir="auto" autoFocus />
      <button className="f-pill">{p.save}</button>
      <button type="button" className="link-btn" onClick={() => setEditing(false)}>{p.close}</button>
      {failed && <span className="plan-err" role="alert">{p.failedSave}</span>}
    </form>
  );
}
