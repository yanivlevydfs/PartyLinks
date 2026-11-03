// Passkeys over /api/passkeys (src/partylinks/passkeys/routes.py). @simplewebauthn/browser turns the server's
// JSON options into navigator.credentials calls and the device's answer back into JSON.
import { browserSupportsWebAuthn, startAuthentication, startRegistration, WebAuthnError } from "@simplewebauthn/browser";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { send } from "../accounts/useMe";

export interface Passkey {
  id: number;
  name: string;
  created_at: string;
  last_used_at: string | null;
}

/** Why a ceremony ended without a result; "cancelled" is the visitor's own choice, shown as nothing. */
export type Failure = "cancelled" | "already" | "unknown" | "blocked" | "failed";

export const supported = () => browserSupportsWebAuthn();

/** The device's kind, to tell passkeys apart on the account page (the visitor can't see the credential ids). */
export function deviceName(ua = navigator.userAgent): string {
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return "iPad";
  if (/Android/.test(ua)) return "Android";
  if (/Macintosh|Mac OS X/.test(ua)) return "Mac";
  if (/Windows/.test(ua)) return "Windows";
  if (/CrOS/.test(ua)) return "Chromebook";
  if (/Linux/.test(ua)) return "Linux";
  return "Passkey";
}

/** NotAllowedError covers the visitor closing the prompt or letting it time out: nothing to say. */
function failure(err: unknown): Failure {
  const cause = err instanceof WebAuthnError ? (err.cause as Error | undefined) : err;
  if (err instanceof WebAuthnError && err.code === "ERROR_AUTHENTICATOR_PREVIOUSLY_REGISTERED") return "already";
  if (cause instanceof Error && (cause.name === "NotAllowedError" || cause.name === "AbortError")) return "cancelled";
  if (err instanceof WebAuthnError) return "failed";
  const status = err instanceof Error ? err.message : "";
  return status === "401" ? "unknown" : status === "403" ? "blocked" : status === "409" ? "already" : "failed";
}

/** Sign in with a passkey this device holds; null on success (the session cookie is set). */
export async function signInWithPasskey(): Promise<Failure | null> {
  try {
    const optionsJSON = await send<Parameters<typeof startAuthentication>[0]["optionsJSON"]>("/api/passkeys/login/options", "POST");
    const credential = await startAuthentication({ optionsJSON: optionsJSON! });
    await send("/api/passkeys/login/verify", "POST", { credential });
    return null;
  } catch (err) {
    return failure(err);
  }
}

export function usePasskeys() {
  return useQuery<Passkey[]>({
    queryKey: ["passkeys"],
    queryFn: async () => {
      const res = await fetch("/api/passkeys", { credentials: "same-origin" });
      if (!res.ok) throw new Error(String(res.status));
      return res.json();
    },
  });
}

/** Add this device's passkey to the signed-in account, and remove one. */
export function usePasskeyActions() {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["passkeys"] });
  return {
    add: async (): Promise<Failure | null> => {
      try {
        const optionsJSON = await send<Parameters<typeof startRegistration>[0]["optionsJSON"]>("/api/passkeys/register/options", "POST");
        const credential = await startRegistration({ optionsJSON: optionsJSON! });
        await send("/api/passkeys/register/verify", "POST", { credential, name: deviceName() });
        return null;
      } catch (err) {
        return failure(err);
      } finally {
        void refresh();
      }
    },
    remove: async (id: number): Promise<boolean> => {
      try {
        await send(`/api/passkeys/${id}`, "DELETE");
        return true;
      } catch {
        return false;
      } finally {
        void refresh();
      }
    },
  };
}
