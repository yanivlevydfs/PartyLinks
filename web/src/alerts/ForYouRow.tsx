// "For you" on the home page: upcoming events matching the signed-in user's alerts, as a home row.
import { Rail } from "../pages/Home";
import { useAlertText } from "./i18n";
import { useAlerts, useForYou } from "./useAlerts";

export function ForYouRow() {
  const t = useAlertText();
  const alerts = useAlerts().data?.items ?? [];
  const on = alerts.some((a) => a.active);
  const q = useForYou(on);
  if (!on || !q.data?.items.length) return null;
  return <Rail title={t.forYou} sub={t.forYouSub} pool={q.data.items} />;
}
