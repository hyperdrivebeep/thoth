import { useLayoutEffect } from "react";
import { hashKey, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { isReadAccessDenied } from "../api/readAccess";

/** A transient failure or manual cache write cannot undo a previous read denial. */
export function useReadAccessProtection(queryKey: QueryKey, error: unknown): boolean {
  const client = useQueryClient();
  const queryHash = hashKey(queryKey);
  const guardKey = ["read-access-blocked", queryHash];
  const guard = useQuery({ queryKey: guardKey, queryFn: () => false, enabled: false, gcTime: Infinity,
    initialData: () => isReadAccessDenied(client.getQueryState(queryKey)?.error) });
  useLayoutEffect(() => {
    const cache = client.getQueryCache();
    const block = () => client.setQueryData(["read-access-blocked", queryHash], true);
    if (isReadAccessDenied(cache.get(queryHash)?.state.error)) block();
    return cache.subscribe(event => {
      if (event.type !== "updated" || event.query.queryHash !== queryHash || cache.get(queryHash) !== event.query) return;
      if (event.action.type === "error" && isReadAccessDenied(event.query.state.error)) block();
      else if (event.action.type === "success" && !event.action.manual) {
        client.setQueryData(["read-access-blocked", queryHash], false);
      }
    });
  }, [client, queryHash]);
  return guard.data || isReadAccessDenied(error);
}
