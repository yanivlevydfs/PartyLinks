// /groups: the signed-in user's groups, one per event (made or answered signed in), soonest first.
// A group goes with its event; the creator deletes it for everyone, a friend leaves it.
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { SignInButton } from "../accounts/AccountSlot";
import { useNoIndex } from "../accounts/AccountPage";
import { send, useMe } from "../accounts/useMe";
import { Hero, Img, Loader, sized, useT, useTitle, when } from "../ui";
import { usePlanText } from "./i18n";
import "./plans.css";
import type { Group } from "./types";
import { useGroups } from "./useGroups";

export default function GroupsPage() {
  const p = usePlanText();
  const { t } = useT();
  useTitle(p.myGroups);
  useNoIndex();
  const me = useMe();
  const qc = useQueryClient();
  const groups = useGroups();
  const [failed, setFailed] = useState(false);
  const remove = async (g: Group) => {
    // Deleting is for everyone in the group, and can't be undone: ask first. Leaving only takes the user's answer.
    if (g.owner && !window.confirm(p.confirmDelete(g.name ?? g.event.title))) return;
    try {
      await send(`/api/me/groups/${encodeURIComponent(g.id)}`, "DELETE");
      setFailed(false);
    } catch {
      setFailed(true);
    }
    await qc.invalidateQueries({ queryKey: ["groups"] });
  };
  return (
    <>
      <Hero title={p.myGroups} />
      <main className="wrap plan">
        <p className="muted">{p.groupsIntro}</p>
        {failed && <p className="plan-err" role="alert">{p.failedSave}</p>}
        {me.isLoading || groups.isLoading ? <Loader /> : !me.data ? <SignInButton next="/groups" /> : !groups.data?.length ? (
          <p className="muted">{p.noGroups} <Link to="/">{p.browse}</Link></p>
        ) : (
          <ul className="groups">
            {groups.data.map((g) => (
              <li key={g.id} className="group-row">
                <Link to={`/plan/${g.id}`} className="plan-option-link">
                  <Img src={sized(g.event.cover_url ?? g.event.image_url, 640)} alt={g.event.title} className="plan-thumb" />
                  <div>
                    <b dir="auto">{g.name ?? g.event.title}{g.options > 1 && ` (+${g.options - 1})`}</b>
                    {g.name && <small dir="auto">{g.event.title}</small>}
                    <small>{when(g.event, t, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</small>
                    <small>
                      {p.going(g.going)} · {p.maybes(g.maybe)}
                      {(g.men > 0 || g.women > 0) && ` · ${p.mix(g.men, g.women)}`}
                      {(g.seats > 0 || g.riders > 0) && ` · 🚗 ${p.seatsOf(g.seats)} · 🙋 ${g.riders}`}
                      {g.answer && ` · ${p.yourAnswer}: ${p[g.answer]}`}
                    </small>
                  </div>
                </Link>
                <button type="button" className="link-btn" onClick={() => remove(g)}>{g.owner ? p.deleteGroup : p.leaveGroup}</button>
              </li>
            ))}
          </ul>
        )}
      </main>
    </>
  );
}
