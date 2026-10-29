// Mirrors partylinks/plans/routes.py (PlanOut, VoteOut, GroupOut).
import type { EventSummary } from "../types";

export type Answer = "yes" | "maybe" | "no";
export type Ride = "drive" | "need";
export type Gender = "male" | "female" | "none"; // "none": would rather not say
export interface Vote { name: string; answer: Answer; gender: Gender | null; mine: boolean; picks: string[]; ride: Ride | null; seats: number | null }
// The API's summary also carries when the group's free entry closes (EventSummaryOut.group_free_entry_until).
export type PlanEvent = EventSummary & { group_free_entry_until?: string | null };
export interface Plan { id: string; name: string | null; owner: boolean; in_groups: boolean; event: PlanEvent; options: PlanEvent[]; votes: Vote[] }
// One row of /api/me/groups (routes.GroupOut).
export interface Group {
  id: string; name: string | null; owner: boolean; event: PlanEvent; options: number;
  answer: Answer | null; going: number; maybe: number; seats: number; riders: number; men: number; women: number;
}
// Plan writes carry our header: signed in, the server then puts the plan in the account's groups
// (a cross-site post can't, and stays anonymous).
export const WRITE_HEADERS = { "content-type": "application/json", "X-Requested-With": "spotx" };
