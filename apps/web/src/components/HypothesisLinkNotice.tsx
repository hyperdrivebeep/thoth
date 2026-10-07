import { Button, Callout } from "@blueprintjs/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { hypothesisLinksKey, recheckHypothesisLinks, type ReasonDistribution } from "../api/hypothesisLink";
import { LinkRecheckDialog } from "./LinkRecheckDialog";
import { linkChangeLine, linkSiblings, recheckLine, type HypothesisLink } from "./hypothesisLinkText";

function RecheckWork({ projectId, link, siblings, distribution, onClose }: {
  projectId: string; link: HypothesisLink; siblings: HypothesisLink[]; distribution?: ReasonDistribution; onClose: () => void;
}) {
  const client = useQueryClient();
  const send = useMutation({
    mutationFn: (input: { ids: string[]; reason: Parameters<typeof recheckHypothesisLinks>[2]; note: string; revision: string | null }) =>
      recheckHypothesisLinks(projectId, input.ids, input.reason, input.note, input.revision),
    onSuccess: async () => { await client.invalidateQueries({ queryKey: hypothesisLinksKey(projectId) }); onClose(); },
  });
  return <LinkRecheckDialog target={link} siblings={siblings} distribution={distribution} pending={send.isPending}
    error={send.error ? "확인을 기록하지 못했습니다. 판정이 그 사이 다시 바뀌었을 수 있으니 화면을 새로 읽은 뒤 다시 시도해 주세요." : null}
    onClose={onClose} onSubmit={(ids, reason, note, revision) => send.mutate({ ids, reason, note, revision })}/>;
}

/** What a hypothesis from a trace row says about its verdict: nothing while current; the old basis, or the person's re-check. */
export function HypothesisLinkNotice({ projectId, link, all = [], distribution }: {
  projectId: string; link: HypothesisLink | undefined; all?: HypothesisLink[]; distribution?: ReasonDistribution;
}) {
  const [open, setOpen] = useState(false);
  if (!link || link.state === "CURRENT") return null;
  const change = linkChangeLine(link);
  const checked = recheckLine(link);
  return <Callout compact intent={link.state === "STALE" ? "warning" : "success"} className="hypothesis-link" role="note">
    {change && <p><strong>{change}</strong></p>}
    {link.subject_title && <small className="muted">대상: 「{link.subject_title}」 {link.subject_kind === "CRITERION" ? "기준" : "요구사항"}</small>}
    {checked && <p>{checked}</p>}
    {link.state === "STALE" && <div><Button small icon="tick" onClick={() => setOpen(true)}>다시 확인</Button>
      <small className="muted"> 이전 근거 기준인 가설은 행동 승인과 실행에 쓰이지 않습니다.</small></div>}
    {open && <RecheckWork projectId={projectId} link={link} siblings={linkSiblings(link, all)} distribution={distribution} onClose={() => setOpen(false)}/>}
  </Callout>;
}
