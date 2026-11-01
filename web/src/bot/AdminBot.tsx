// Admin tab "bot": is the Telegram bot set up and running, how much it is used (people, messages, searches,
// ticket clicks from its cards), what stands now (chats, alerts, reminders, watches, digest), what people
// search and what found nothing. Data: GET /admin/api/bot (partylinks.bot.admin_routes).
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Loader } from "../ui";
import { call, Card, Stat } from "../pages/Admin";

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const SERIES = [["people", "People"], ["messages", "Messages"], ["searches", "Searches"], ["clicks", "Ticket clicks"]] as const;

export function AdminBot() {
  const [days, setDays] = useState(30);
  const [series, setSeries] = useState<(typeof SERIES)[number][0]>("people");
  const q = useQuery({ queryKey: ["bot", days], queryFn: () => call(`/admin/api/bot?days=${days}`), refetchInterval: 30_000 });
  const d = q.data;
  if (!d) return q.isError ? <p className="adm-err">{String(q.error)}</p> : <Loader />;
  const s = d.setup, now = d.now;
  const peak = Math.max(1, ...d.per_day.map((x: any) => x[series]));
  const kinds = Object.entries(d.per_kind as Record<string, number>);
  const terms = (title: string, rows: any[]) => (
    <Card title={title}>
      <table className="adm-table">
        <thead><tr><th>Search</th><th>Times</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.q}><td>{r.q}</td><td>{r.searches}</td></tr>)}</tbody>
      </table>
    </Card>
  );
  return (
    <>
      <div className="adm-bar">
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Period">
          {[1, 7, 30, 90, 365].map((n) => <option key={n} value={n}>Last {n} day{n > 1 ? "s" : ""}</option>)}
        </select>
      </div>
      <div className="adm-grid">
        <Card title="Set up">
          <div className="adm-stats">
            <Stat label="Running" value={s.running ? "✅" : "❌"} hint={s.enabled ? "bot.enabled" : "bot.enabled is off"} />
            <Stat label="Token" value={s.token ? "✅" : "❌"} hint={s.token ? "set" : "PARTYLINKS_BOT__TELEGRAM_TOKEN"} />
            <Stat label="Secret" value={s.secret ? "✅" : "❌"} hint={s.secret ? "set" : "PARTYLINKS_BOT__TELEGRAM_WEBHOOK_SECRET (16+)"} />
            <Stat label="Webhook" value={<small>{s.webhook}</small>} />
            <Stat label="Channel" value={<small>{s.channel || "—"}</small>} hint="bot.channel_id" />
            <Stat label="Digest" value={<small>{WEEKDAYS[s.digest.weekday]} {s.digest.hour}:00</small>} />
          </div>
        </Card>
        <Card title="Now">
          <div className="adm-stats">
            <Stat label="Chats" value={now.chats} hint={`${now.blocked} blocked the bot`} />
            <Stat label="Active, 7 days" value={now.active_7d} hint={`${now.new_7d} new`} />
            <Stat label="Alerts 🔔" value={now.alerts} />
            <Stat label="Reminders ⏰" value={now.reminders} />
            <Stat label="Watches 👁" value={now.watches} />
            <Stat label="Digest 📬" value={now.digest} hint="subscribed" />
            <Stat label="Channel posts" value={now.channel_posts} hint="last 60 days" />
          </div>
        </Card>
        <Card title={`Last ${d.days} days`}>
          <div className="adm-stats">
            <Stat label="People" value={d.people} />
            <Stat label="Messages" value={d.per_kind.message ?? 0} />
            <Stat label="Searches" value={d.per_kind.search ?? 0} hint={`${d.top_misses.reduce((n: number, r: any) => n + r.searches, 0)} found nothing`} />
            <Stat label="Ticket clicks" value={d.ticket_clicks} hint="from the bot's cards (/go ?at=tg)" />
            <Stat label="Inline" value={d.per_kind.inline ?? 0} hint="@bot in other chats" />
          </div>
        </Card>
        <Card title="Per day" wide>
          <div className="adm-bar">
            {SERIES.map(([key, name]) => (
              <button key={key} className={"adm-btn" + (series === key ? " on" : "")} onClick={() => setSeries(key)}>{name}</button>
            ))}
          </div>
          <div className="adm-chart" role="img" aria-label={`${series} per day, peak ${peak}`}>
            {d.per_day.map((x: any) => (
              <span key={x.date} style={{ height: `${(x[series] / peak) * 100}%` }} title={`${x.date}: ${x[series]}`} />
            ))}
          </div>
        </Card>
        <Card title="What it did">
          <table className="adm-table">
            <thead><tr><th>Kind</th><th>Times</th></tr></thead>
            <tbody>{kinds.map(([k, n]) => <tr key={k}><td>{k}</td><td>{n}</td></tr>)}</tbody>
          </table>
        </Card>
        {terms("Top searches", d.top)}
        {terms("Found nothing", d.top_misses)}
      </div>
    </>
  );
}
