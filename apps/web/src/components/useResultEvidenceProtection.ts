import { useCallback, useEffect } from "react";
import { hashKey, useQuery, useQueryClient, type InfiniteData, type QueryKey } from "@tanstack/react-query";
import type { ResultEvidencePage } from "../api/resultEvidence";

export function consistentEvidencePages(pages: ResultEvidencePage[]): boolean {
  const first = pages[0];
  return Boolean(first && first.offset === 0 && pages.every((page, index) => page.actor_scope_digest === first.actor_scope_digest &&
    page.result_revision_digest === first.result_revision_digest && page.selected_count === first.selected_count &&
    (index === 0 || page.offset === pages[index - 1].next_offset)));
}

/** A cancelled refetch must not restore bytes rejected by an earlier read. */
export function useResultEvidenceProtection(queryKey: QueryKey) {
  const client = useQueryClient();
  const queryHash = hashKey(queryKey);
  const guardKey = ["result-evidence-blocked", queryHash];
  const guard = useQuery<"NONE" | "READ_FAILED" | "SCOPE_CHANGED">({ queryKey: guardKey, queryFn: () => "NONE", initialData: "NONE", enabled: false, gcTime: 0 });
  const block = useCallback((reason: "READ_FAILED" | "SCOPE_CHANGED" = "READ_FAILED") => {
    client.setQueryData(["result-evidence-blocked", queryHash], reason);
    const query = client.getQueryCache().get(queryHash);
    if (query) client.setQueryData<InfiniteData<ResultEvidencePage>>(query.queryKey, { pages: [], pageParams: [] });
  }, [client, queryHash]);
  useEffect(() => client.getQueryCache().subscribe(event => {
    if (event.type !== "updated" || event.query.queryHash !== queryHash || event.action.type !== "success" || event.action.manual) return;
    const data = event.query.state.data as InfiniteData<ResultEvidencePage> | undefined;
    if (data && consistentEvidencePages(data.pages)) client.setQueryData(["result-evidence-blocked", queryHash], "NONE");
    else block("SCOPE_CHANGED");
  }), [client, queryHash, block]);
  return { blocked: guard.data !== "NONE", reason: guard.data, block };
}
