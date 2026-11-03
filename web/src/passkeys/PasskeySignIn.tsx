// "Sign in with fingerprint / face": beside Continue with Google / Facebook, for a device that already holds
// a passkey for this site. On success the page reloads at ``next``, as after Google, so a pending action
// (the save heart, "Save as alert") completes the same way.
import { useState } from "react";
import { signInWithPasskey, supported, type Failure } from "./passkeys";
import { usePasskeyText } from "./i18n";
import "./passkeys.css";

function Fingerprint() {
  return (
    <svg className="g-logo" viewBox="0 0 24 24" aria-hidden width="20" height="20" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round">
      <path d="M12 11v3a8 8 0 0 1-1.5 4.7" /><path d="M8.5 7.6A4 4 0 0 1 16 10v2.5a12 12 0 0 1-.6 3.8" />
      <path d="M8 10.5v2a6 6 0 0 1-1.4 3.8" /><path d="M5.3 6.3A8 8 0 0 1 20 10v2" /><path d="M4 10.5v1" />
      <path d="M19.4 16.5a14 14 0 0 1-1.1 3" /><path d="M14 18.6a10 10 0 0 1-.9 2.4" />
    </svg>
  );
}

export function PasskeySignIn({ next = location.pathname + location.search }: { next?: string }) {
  const t = usePasskeyText();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Exclude<Failure, "cancelled"> | null>(null);
  if (!supported()) return null;
  const go = async () => {
    setBusy(true);
    setError(null);
    const failed = await signInWithPasskey();
    if (failed === null) return location.assign(next);
    setBusy(false);
    if (failed !== "cancelled") setError(failed === "already" ? "failed" : failed);
  };
  return (
    <>
      <button type="button" className="google-btn pk-btn" onClick={go} disabled={busy} aria-busy={busy}>
        <Fingerprint /> <span>{busy ? t.working : t.signIn}</span>
      </button>
      {error && <p className="pk-error" role="alert">{t[error]}</p>}
    </>
  );
}
