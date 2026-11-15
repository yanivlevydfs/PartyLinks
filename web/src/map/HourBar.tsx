// The evening's hour bar over the map: drag (or play) from 18:00 to 04:00, and only what starts
// in those two hours stays lit. "All hours" puts every pin back.
import { useEffect, useState } from "react";
import { HOURS } from "./time";
import { useMapText } from "./i18n";

const pad = (h: number) => `${String(h).padStart(2, "0")}:00`;

export function HourBar({ hour, onHour, count }: { hour: number | null; onHour: (h: number | null) => void; count: number }) {
  const m = useMapText();
  const [playing, setPlaying] = useState(false);
  const index = hour == null ? -1 : HOURS.indexOf(hour);

  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => {
      const next = HOURS.indexOf(hour ?? -1) + 1;
      if (next >= HOURS.length) { setPlaying(false); return; }
      onHour(HOURS[next]);
    }, 1400);
    return () => clearInterval(id);
  }, [playing, hour, onHour]);

  return (
    <div className="map-hours" role="group" aria-label={m.hours}>
      <button className="map-hours-play" onClick={() => { if (!playing && index === HOURS.length - 1) onHour(HOURS[0]); setPlaying(!playing); }}
        aria-label={playing ? m.pause : m.play}>{playing ? "❚❚" : "▶"}</button>
      {/* The far left stop (-1) is "all hours": 18:00 is a stop of its own. */}
      <input type="range" min={-1} max={HOURS.length - 1} step={1} value={index} dir="ltr"
        aria-valuetext={hour == null ? m.allHours : pad(hour)}
        onChange={(ev) => { setPlaying(false); const i = Number(ev.target.value); onHour(i < 0 ? null : HOURS[i]); }} />
      <span className="map-hours-label">
        {hour == null ? m.allHours : <><b dir="ltr">{pad(hour)}–{pad((hour + 2) % 24)}</b> · {m.events(count)}</>}
      </span>
      {hour != null && <button className="map-hours-x" onClick={() => { setPlaying(false); onHour(null); }} aria-label={m.allHours}>✕</button>}
    </div>
  );
}
