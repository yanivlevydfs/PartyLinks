// The signed-in user's groups (GET /api/me/groups), one cached request shared by /groups and every
// "who's coming?" button; nothing is fetched signed out.
import { useQuery } from "@tanstack/react-query";
import { useMe } from "../accounts/useMe";
import type { Group } from "./types";

export function useGroups() {
  const me = useMe().data;
  return useQuery<Group[]>({
    queryKey: ["groups"],
    queryFn: () => fetch("/api/me/groups", { credentials: "same-origin" }).then((r) => (r.ok ? r.json() : [])),
    enabled: !!me,
    staleTime: 60_000,
  });
}
