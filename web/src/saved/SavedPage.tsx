// /saved: the saved events, soonest first. Signed in from the account; signed out from this device.
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { SignInButton } from "../accounts/AccountSlot";
import { useNoIndex } from "../accounts/AccountPage";
import { useAccountsOn, useMe } from "../accounts/useMe";
import type { EventSummary } from "../types";
import { Card, Hero, Loader, useTitle } from "../ui";
import { useSavedText } from "./i18n";
import { useLocalSaved, useServerSaved } from "./useSaved";

function useDeviceCards(ids: string[]) {
  return useQuery<{ items: EventSummary[] }>({
    queryKey: ["saved-cards", ids],
    queryFn: () => fetch(`/events/cards?${ids.slice(0, 100).map((i) => `ids=${encodeURIComponent(i)}`).join("&")}`).then((r) => r.json()),
    enabled: ids.length > 0,
  });
}

export default function SavedPage() {
  const t = useSavedText();
  useTitle(t.title);
  useNoIndex();
  const on = useAccountsOn();
  const me = useMe();
  const server = useServerSaved();
  const local = useLocalSaved((s) => s.ids);
  const device = useDeviceCards(me.data ? [] : local);
  const loading = me.isLoading || (me.data ? server.isLoading : local.length > 0 && device.isLoading);
  const items = (me.data ? server.data?.items : device.data?.items) ?? [];

  return (
    <>
      <Hero title={t.title} />
      <main className="wrap">
        {!me.data && on && (
          <div className="saved-note">
            <p>{t.device}</p>
            <SignInButton next="/saved" />
          </div>
        )}
        {loading ? <Loader /> : items.length ? (
          <div className="grid">{items.map((e) => <Card key={e.id} e={e} />)}</div>
        ) : (
          <p className="muted">{t.empty} <Link to="/">{t.browse}</Link></p>
        )}
      </main>
    </>
  );
}
