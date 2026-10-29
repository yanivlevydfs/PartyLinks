// After Google: "?welcome=1" (first sign-in) says hello, "?signin=..." says why it didn't work.
// Closing drops the parameter from the address.
import { useEffect, useState } from "react";
import { useAccountText } from "./i18n";
import { useMe } from "./useMe";

function dropParams(...names: string[]) {
  const url = new URL(location.href);
  names.forEach((n) => url.searchParams.delete(n));
  history.replaceState(history.state, "", url.pathname + url.search + url.hash);
}

export function WelcomeSheet() {
  const t = useAccountText();
  const me = useMe().data;
  const [params] = useState(() => new URLSearchParams(location.search));
  const [open, setOpen] = useState(params.has("welcome") || params.has("signin"));
  const close = () => { setOpen(false); dropParams("welcome", "signin"); };
  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [open]);
  if (!open) return null;
  const error = params.get("signin");
  if (!error && !me) return null;
  return (
    <div className="sheet-back" onClick={close}>
      <div className="sheet" role="dialog" aria-modal="true" aria-labelledby="welcome-title" onClick={(e) => e.stopPropagation()}>
        {error ? (
          <p id="welcome-title" role="alert">{t.signin[error] ?? t.signin.failed}</p>
        ) : (
          <>
            <h2 id="welcome-title" dir="auto">{t.welcomeTitle(me!.name.split(" ")[0])}</h2>
            <p>{t.welcomeText}</p>
          </>
        )}
        <button className="c-btn" onClick={close} autoFocus>{t.close}</button>
      </div>
    </div>
  );
}
