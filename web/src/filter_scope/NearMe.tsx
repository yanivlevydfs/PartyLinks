// "Near me": the browser's position, turned into our region and city by the server (/locate).
import { useState } from "react";
import { Chip, useT } from "../ui";
import { useFilters } from "./scope";

export type Where = { region: string | null; city: string | null };

/** The visitor's region and city, or null (no permission, no fix, outside our map). */
export function locate(): Promise<Where | null> {
  return new Promise((done) => {
    if (!navigator.geolocation) return done(null);
    navigator.geolocation.getCurrentPosition(
      async ({ coords }) => {
        try {
          const res = await fetch(`/locate?lat=${coords.latitude.toFixed(3)}&lng=${coords.longitude.toFixed(3)}`);
          done(res.ok ? await res.json() : null);
        } catch {
          done(null);
        }
      },
      () => done(null),
      { timeout: 10_000, maximumAge: 10 * 60_000 },
    );
  });
}

/** Area-row chip: picks the visitor's region (and city outside Tel Aviv). */
export function NearChip() {
  const { t } = useT();
  const { set } = useFilters();
  const [state, setState] = useState<"idle" | "busy" | "fail">("idle");
  const click = async () => {
    setState("busy");
    const where = await locate();
    if (!where?.region) return setState("fail");
    setState("idle");
    set({ regions: [where.region], areas: [], city: where.region === "tel_aviv" ? null : where.city });
  };
  return (
    <Chip on={false} onClick={click} className="near">
      📍 {state === "busy" ? t.quiz.locating : state === "fail" ? t.quiz.noLocation : t.quiz.near}
    </Chip>
  );
}
