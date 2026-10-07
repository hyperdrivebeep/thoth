import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Callout } from "@blueprintjs/core";
import { objectValue, textValue } from "../api/presentation";
import { rpc } from "../api/rpcClient";
import { HypothesisCompare } from "./HypothesisCompare";
import { parseRequests, type ReviewControls, type ReviewTarget } from "./judgmentReview";
import { ReviewDialog } from "./ReviewDialog";
import { useHypothesisLinks } from "./useHypothesisLinks";
import { useDiscrimination, useLessons, useSameHypotheses } from "./useJudgmentRecords";

type SendInput = { hypothesisId: string; evidenceRef: string | null; reasons: string[]; note: string; digest?: string };

/** Hypothesis comparison with review requests: reads the thread's requests and sends new ones. */
export function HypothesisCompareLive({ result, projectId, threadId }: { result: Record<string, unknown>; projectId: string; threadId: string }) {
  const client = useQueryClient();
  const key = ["judgment-review", projectId, threadId];
  const list = useQuery({ queryKey: key, retry: false,
    queryFn: ({ signal }) => rpc<{ requests: unknown }>("hypothesis/review/list", { project_id: projectId, thread_id: threadId }, crypto.randomUUID(), signal),
    refetchInterval: query => parseRequests(query.state.data?.value.requests).some(item => item.status === "REVIEWING") ? 3000 : false });
  const requests = parseRequests(list.data?.value.requests);
  const [target, setTarget] = useState<ReviewTarget | null>(null);
  const linked = useHypothesisLinks(projectId);
  const discrimination = useDiscrimination(projectId);
  const lessons = useLessons(projectId);
  const same = useSameHypotheses(projectId);
  const send = useMutation({
    mutationFn: async (input: SendInput) => {
      const digest = input.digest ?? textValue(objectValue((await rpc<{ hypothesis: unknown }>("hypothesis/read",
        { project_id: projectId, hypothesis_id: input.hypothesisId }, crypto.randomUUID())).value.hypothesis).revision_digest);
      return rpc("hypothesis/review/request", { project_id: projectId, thread_id: threadId, hypothesis_id: input.hypothesisId,
        hypothesis_revision_digest: digest, ...(input.evidenceRef ? { evidence_ref: input.evidenceRef } : {}),
        reason_codes: input.reasons, note: input.note }, crypto.randomUUID());
    },
    onSuccess: async () => {
      setTarget(null);
      await Promise.all([key, ["research", projectId, threadId], ["conversation", projectId, threadId]].map(queryKey => client.invalidateQueries({ queryKey })));
    },
  });
  const controls: ReviewControls = {
    requests,
    onRequest: next => { send.reset(); setTarget(next); },
    onResend: item => send.mutate({ hypothesisId: item.hypothesis_id, evidenceRef: item.evidence_ref, reasons: item.reason_codes, note: item.note, digest: item.hypothesis_revision_digest }),
  };
  return <>
    {list.error && <Callout compact intent="warning" role="status">재검토 요청 상태를 읽지 못했습니다.</Callout>}
    {!target && send.error && <Callout compact intent="danger" role="alert">재검토 요청을 보내지 못했습니다. {send.error.message}</Callout>}
    <HypothesisCompare result={result} review={controls} projectId={projectId} links={linked.links} distribution={linked.distribution} canDraft discrimination={discrimination.items} lessons={lessons.lessons} same={same.same}/>
    <ReviewDialog target={target} pending={send.isPending} error={target && send.error ? send.error.message : null} onClose={() => setTarget(null)}
      onSubmit={(reasons, note) => target && send.mutate({ hypothesisId: target.hypothesisId, evidenceRef: target.evidenceRef, reasons, note })}/>
  </>;
}
