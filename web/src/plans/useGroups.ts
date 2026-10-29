// The signed-in user's groups (GET /api/me/groups), one cached request shared by /groups and every
// "who's coming?" button; nothing is fetched signed out.
import { useQuery } from "@tanstack/react-query";
import { useMe } from "../accounts/useMe";
import type { Group } from "./types";

export function useGroups() {
  const me = useMe().data;
  return useQuery<Group[]>({
    queryKey: ["groups", me?.id], // per account: a shared device never shows the previous user's groups
    queryFn: async () => {
      const res = await fetch("/api/me/groups", { credentials: "same-origin" });
      if (!res.ok) throw new Error(String(res.status)); // a 429 is not "no groups"
      return res.json();
    },
    enabled: !!me,
    staleTime: 60_000,
  });
}
