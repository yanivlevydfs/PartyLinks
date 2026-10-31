// Admin "config" tab: the parameters the admin changes, saved in the database on top of settings.yaml
// (partylinks/config_store). A deploy replaces the file, never these. Priority: environment > database > file.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Loader, TZ } from "../ui";
import { call, post } from "../pages/Admin";
import "./overrides.css";

interface Key { key: string; value: unknown; file_value: unknown }
interface Version { id: number; created_at: string; note: string | null; keys: number }
interface View { yaml: string; keys: Key[]; in_force: boolean; versions: Version[] }
interface Saved extends View { applied: boolean; reason?: string; restart_required?: string[]; version: number }

const show = (v: unknown) => (v === null || v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));
const at = (iso: string) => new Date(iso).toLocaleString("en-GB", { timeZone: TZ, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const EXAMPLE = "# Only the values you change, e.g.:\n# retention:\n#   assumed_duration_hours: 4\n# sources:\n#   meety:\n#     enabled: false\n";

export function Overrides() {
  const qc = useQueryClient();
  const view = useQuery({ queryKey: ["config-overrides"], queryFn: () => call<View>("/admin/api/config/overrides"), refetchOnWindowFocus: false });
  const file = useQuery({ queryKey: ["config"], queryFn: () => call("/admin/api/config"), refetchOnWindowFocus: false });
  const [text, setText] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [check, setCheck] = useState<{ ok: boolean; errors: string[] } | null>(null);
  const [saved, setSaved] = useState<Saved | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const yamlText = text ?? view.data?.yaml ?? "";
  const dirty = text != null && text !== view.data?.yaml;

  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setErr(""); setSaved(null);
    try { await fn(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const done = async (r: Saved) => { setSaved(r); setCheck(null); setText(null); setNote(""); await qc.invalidateQueries(); };
  const validate = () => run(async () => setCheck(await post("/admin/api/config/overrides/validate", { yaml: yamlText })));
  const save = () => run(async () => done(await call<Saved>("/admin/api/config/overrides", { method: "PUT", body: JSON.stringify({ yaml: yamlText, note: note || null }) })));
  const reset = (key: string) => run(async () => done(await call<Saved>(`/admin/api/config/overrides/key/${encodeURIComponent(key)}`, { method: "DELETE" })));
  const loadVersion = (id: string) => id && run(async () => { setText((await call<{ yaml: string }>(`/admin/api/config/overrides/versions/${id}`)).yaml); setCheck(null); });

  if (view.isError) return <p className="adm-err">{String(view.error)}</p>;
  if (!view.data) return <Loader />;
  const v = view.data;
  return (
    <div className="adm-config">
      <h3>Overrides (saved in the database)</h3>
      {v.keys.length > 0 ? (
        <table className="adm-table">
          <thead><tr><th>Setting</th><th>Value now</th><th>settings.yaml</th><th /></tr></thead>
          <tbody>
            {v.keys.map((k) => (
              <tr key={k.key}>
                <td><code>{k.key}</code></td><td><b>{show(k.value)}</b></td><td>{show(k.file_value)}</td>
                <td><button className="adm-btn ghost" onClick={() => reset(k.key)} disabled={busy}>Back to file</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <p className="adm-note">Nothing overridden: every setting comes from settings.yaml.</p>}
      {!v.in_force && <p className="adm-warn">The newest saved version is not in force in this process yet: press Save again to apply.</p>}
      <div className="adm-bar">
        <input className="adm-input" placeholder="What changed (optional)" value={note} onChange={(e) => setNote(e.target.value)} aria-label="Note" />
        <button className="adm-btn ghost" onClick={validate} disabled={busy}>Validate</button>
        <button className="adm-btn" onClick={save} disabled={busy || !dirty}>Save &amp; apply</button>
        <button className="adm-btn ghost" onClick={() => { setText(null); setCheck(null); }} disabled={busy || !dirty}>Discard changes</button>
        {v.versions.length > 0 && (
          <select value="" onChange={(e) => loadVersion(e.target.value)} aria-label="Load a saved version">
            <option value="">Load version…</option>
            {v.versions.map((x) => <option key={x.id} value={x.id}>#{x.id} · {at(x.created_at)} · {x.keys} keys{x.note ? ` · ${x.note}` : ""}</option>)}
          </select>
        )}
      </div>
      {check && (check.ok ? <p className="adm-ok">✓ Valid</p> : <pre className="adm-err">{check.errors.join("\n")}</pre>)}
      {err && <pre className="adm-err">{err}</pre>}
      {saved && (
        <p className={saved.applied ? "adm-ok" : "adm-warn"}>
          Saved as version #{saved.version}. {saved.applied ? "Applied now." : `Not applied yet: ${saved.reason}`}
          {(saved.restart_required?.length ?? 0) > 0 && ` Restart the server for: ${saved.restart_required!.join(", ")}.`}
        </p>
      )}
      <textarea className="adm-yaml" spellCheck={false} value={yamlText} placeholder={EXAMPLE} onChange={(e) => setText(e.target.value)} aria-label="Overrides YAML" />
      <p className="adm-note">
        Any setting of settings.yaml can be overridden here, only the keys you write; lists replace the file's list whole. Saving checks
        file + overrides as the server loads them, keeps the previous versions, and applies in both processes without a restart.
        Environment variables (<code>PARTYLINKS_*</code>) still win. <code>database</code> and <code>admin</code> are not settable here.
      </p>
      {file.data && (
        <>
          <details className="adm-effective">
            <summary>settings.yaml (the base, from git: <code>{file.data.path}</code>)</summary>
            <pre>{file.data.yaml}</pre>
          </details>
          <details className="adm-effective">
            <summary>Settings in use right now (file + database + environment, secrets hidden)</summary>
            <pre>{JSON.stringify(file.data.effective, null, 2)}</pre>
          </details>
        </>
      )}
    </div>
  );
}
