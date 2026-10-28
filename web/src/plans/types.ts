// Mirrors partylinks/plans/routes.py (PlanOut, VoteOut).
import type { EventSummary } from "../types";

export type Answer = "yes" | "maybe" | "no";
export type Ride = "drive" | "need";
export interface Vote { name: string; answer: Answer; mine: boolean; picks: string[]; ride: Ride | null; seats: number | null }
export interface Plan { id: string; event: EventSummary; options: EventSummary[]; votes: Vote[] }
