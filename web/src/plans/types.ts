// Mirrors partylinks/plans/routes.py (PlanOut, VoteOut).
import type { EventSummary } from "../types";

export type Answer = "yes" | "maybe" | "no";
export type Ride = "drive" | "need";
export interface Vote { name: string; answer: Answer; mine: boolean; picks: string[]; ride: Ride | null; seats: number | null }
// The API's summary also carries when the group's free entry closes (EventSummaryOut.group_free_entry_until).
export type PlanEvent = EventSummary & { group_free_entry_until?: string | null };
export interface Plan { id: string; event: PlanEvent; options: PlanEvent[]; votes: Vote[] }
