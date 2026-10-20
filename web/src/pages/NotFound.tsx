import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useT, useTitle } from "../ui";

// The party stopped: the "0" of 404 is a record that scratched to a halt. "Drop the beat" spins it
// back up and the floor (ball, beams, speakers, equalizer) comes alive.
export default function NotFound() {
  const { t } = useT();
  const nf = t.nf;
  const [playing, setPlaying] = useState(false);
  useTitle(t.notFound);
  useEffect(() => {
    const id = setTimeout(() => setPlaying(true), 2600); // the music comes back on its own
    return () => clearTimeout(id);
  }, []);

  return (
    <main className={`nf${playing ? " on" : ""}`}>
      <div className="nf-beams" aria-hidden="true" />
      <div className="nf-ball" aria-hidden="true">
        <span className="nf-string" />
        <svg viewBox="0 0 100 100" className="nf-ball-svg">
          <defs>
            <pattern id="nf-tiles" width="12" height="12" patternUnits="userSpaceOnUse">
              <rect width="12" height="12" fill="#2a1d45" />
              <rect x="1" y="1" width="10" height="10" fill="#d9d0ee" />
              <rect x="1" y="1" width="5" height="5" fill="#ffffff" />
            </pattern>
            <clipPath id="nf-round"><circle cx="50" cy="50" r="46" /></clipPath>
            <radialGradient id="nf-shade" cx="35%" cy="30%" r="75%">
              <stop offset="0" stopColor="#fff" stopOpacity=".05" />
              <stop offset="1" stopColor="#06040b" stopOpacity=".7" />
            </radialGradient>
          </defs>
          <g clipPath="url(#nf-round)">
            <rect className="nf-tiles" x="-24" y="0" width="148" height="100" fill="url(#nf-tiles)" />
            <rect className="nf-hot" x="25" y="13" width="10" height="10" fill="var(--pink)" />
            <rect className="nf-hot b" x="61" y="49" width="10" height="10" fill="var(--cyan)" />
            <rect className="nf-hot c" x="37" y="61" width="10" height="10" fill="var(--violet)" />
            <circle cx="50" cy="50" r="46" fill="url(#nf-shade)" />
          </g>
          <circle cx="50" cy="50" r="46" fill="none" stroke="#fff" strokeOpacity=".25" />
        </svg>
        <i className="nf-spark s1">✦</i>
        <i className="nf-spark s2">✦</i>
        <i className="nf-spark s3">✦</i>
      </div>

      <div className="nf-stage">
        <Speaker />
        <h1 className="nf-code" aria-label="404">
          <span>4</span>
          <span className="nf-vinyl" aria-hidden="true">
            <span className="nf-label">{nf.label}</span>
          </span>
          <span>4</span>
        </h1>
        <Speaker />
      </div>

      <div className="nf-eq" aria-hidden="true">
        {Array.from({ length: 24 }, (_, i) => (
          <span key={i} style={{ animationDelay: `${-((i * 37) % 11) / 10}s`, animationDuration: `${0.6 + ((i * 7) % 5) / 10}s` }} />
        ))}
      </div>

      <div className="nf-copy">
        <p className="nf-title">{playing ? nf.titleOn : nf.title}</p>
        <p className="nf-text">{nf.text}</p>
        <div className="nf-actions">
          <Link className="nf-btn" to="/">{nf.home}</Link>
          <Link className="nf-btn ghost" to="/tonight">{nf.tonight}</Link>
          <button className="nf-btn ghost" onClick={() => setPlaying((p) => !p)} aria-pressed={playing}>
            {playing ? nf.stop : nf.drop}
          </button>
        </div>
      </div>
    </main>
  );
}

function Speaker() {
  return (
    <div className="nf-speaker" aria-hidden="true">
      <span className="nf-tweeter" />
      <span className="nf-woofer" />
    </div>
  );
}
