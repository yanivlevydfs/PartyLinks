// Buttons floating over the map: tonight near me, near me, the list of what is in view, my saved events,
// share this view, and the pin colours' legend.
import { useState } from "react";
import type { LatLng } from "./geo";
import { useMapText } from "./i18n";
import { useStore } from "../store";

function useLocate(onLocated: (where: LatLng) => void) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const locate = (then?: () => void) => {
    if (!navigator.geolocation) return setFailed(true);
    setBusy(true);
    setFailed(false);
    navigator.geolocation.getCurrentPosition(
      (p) => { setBusy(false); onLocated([p.coords.latitude, p.coords.longitude]); then?.(); },
      () => { setBusy(false); setFailed(true); },
      { enableHighAccuracy: false, timeout: 10_000, maximumAge: 300_000 },
    );
  };
  return { busy, failed, locate };
}

export function MapControls({ inView, listOpen, onList, onLocated, onTonight, savedCount, savedOnly, onSavedOnly, sharePath }: {
  inView: number;
  listOpen: boolean;
  onList: () => void;
  onLocated: (where: LatLng) => void;
  onTonight: () => void;
  savedCount: number;
  savedOnly: boolean;
  onSavedOnly: () => void;
  sharePath: () => string;
}) {
  const m = useMapText();
  // Free entry only: the filter's own "free" switch (same store), one tap away on the map.
  const free = useStore((s) => s.filters.freeOnly);
  const setFilters = useStore((s) => s.setFilters);
  const { busy, failed, locate } = useLocate(onLocated);
  const [copied, setCopied] = useState(false);
  const share = async () => {
    const url = location.origin + sharePath();
    if (navigator.share && matchMedia("(pointer: coarse)").matches) return navigator.share({ title: document.title, url }).catch(() => {});
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard blocked: nothing to do */ }
  };
  return (
    <div className="map-ctrls">
      <button className="map-fab hot" onClick={() => locate(onTonight)} disabled={busy}>
        <span aria-hidden>🌙</span> {m.tonightNear}
      </button>
      <button className="map-fab" onClick={() => locate()} disabled={busy} title={failed ? m.noLocation : undefined}>
        <span aria-hidden>◎</span> {busy ? m.locating : failed ? m.noLocation : m.nearMe}
      </button>
      <button className={"map-fab" + (listOpen ? " on" : "")} aria-pressed={listOpen} onClick={onList}>
        <span aria-hidden>☰</span> {m.listInView(inView)}
      </button>
      <button className={"map-fab" + (free ? " on free" : "")} aria-pressed={free} onClick={() => setFilters({ freeOnly: !free })}>
        <span aria-hidden>₪0</span> {m.freeOnly}
      </button>
      {savedCount > 0 && (
        <button className={"map-fab" + (savedOnly ? " on gold" : "")} aria-pressed={savedOnly} onClick={onSavedOnly}>
          <span aria-hidden>♥</span> {m.savedOnly} · {savedCount}
        </button>
      )}
      <button className="map-fab" onClick={share} aria-live="polite">
        <span aria-hidden>↗</span> {copied ? m.copied : m.share}
      </button>
      <div className="map-legend" aria-hidden>
        <span><i className="dot live" />{m.legendLive}</span>
        <span><i className="dot today" />{m.legendToday}</span>
        <span><i className="dot" />{m.legendLater}</span>
        {savedCount > 0 && <span><i className="dot saved" />{m.legendSaved}</span>}
      </div>
    </div>
  );
}
