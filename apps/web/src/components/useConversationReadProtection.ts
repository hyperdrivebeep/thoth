import { useCallback, useEffect } from "react";
import { useQuery, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import type { ConversationPage } from "../api/conversation";

/** UI read protection; cleared only by a complete successful history refetch. */
export function useConversationReadProtection(projectId: string, threadId: string) {
  const client = useQueryClient();
  const protection = useQuery({
    queryKey: ["conversation-read-blocked", projectId, threadId],
    queryFn: () => false, initialData: false, enabled: false, gcTime: Infinity,
  });
  const block = useCallback(() => {
    client.setQueryData(["conversation-read-blocked", projectId, threadId], true);
    // Remove answer bytes immediately, including pages read before the rejected page.
    client.setQueryData<InfiniteData<ConversationPage>>(["conversation", projectId, threadId], {
      pages: [{ turns: [], next: null, limited: false, supported: true, redacted: true }], pageParams: [null],
    });
  }, [client, projectId, threadId]);
  useEffect(() => client.getQueryCache().subscribe(event => {
    if (event.type !== "updated" || event.action.type !== "success" || event.action.manual) return;
    const key = event.query.queryKey;
    if (key[0] !== "conversation" || key[1] !== projectId || key[2] !== threadId) return;
    const data = event.query.state.data as InfiniteData<ConversationPage> | undefined;
    if (data?.pages.length && data.pages.every(page => !page.redacted)) {
      client.setQueryData(["conversation-read-blocked", projectId, threadId], false);
    }
  }), [client, projectId, threadId]);
  return { blocked: protection.data, block };
}
