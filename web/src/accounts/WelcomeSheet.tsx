// After Google: "?welcome=1" (first sign-in) says hello, "?signin=..." says why it didn't work.
// Closing drops the parameter from the address.
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAccountText } from "./i18n";
import { send, useMe, type Me } from "./useMe";

function dropParams(...names: string[]) {
  const url = new URL(location.href);
  names.forEach((n) => url.searchParams.delete(n));
  history.replaceState(history.state, "", url.pathname + url.search + url.hash);
}

export function WelcomeSheet() {
  const t = useAccountText();
  const me = useMe().data;
  const qc = useQueryClient();
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
            {/* Unticked: the weekly email needs the user's own yes (Communications Law §30A). */}
            <label className="acc-check">
              <input type="checkbox" checked={me!.digest_opt_in}
                onChange={() => send<Me>("/api/me", "PATCH", { digest_opt_in: !me!.digest_opt_in }).then((m) => qc.setQueryData(["me"], m)).catch(() => {})} />
              <span>{t.digest}</span>
            </label>
            <Link to="/alerts" onClick={close}>{t.welcomeAlerts} ›</Link>
          </>
        )}
        <button className="c-btn" onClick={close} autoFocus>{t.close}</button>
      </div>
    </div>
  );
}
