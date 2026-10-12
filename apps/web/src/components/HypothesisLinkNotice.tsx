import { t } from "../i18n/locale";
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
    error={send.error ? t("hypothesisLink.recordFailed") : null}
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
    {link.subject_title && <small className="muted">{t("labels.target")} 「{link.subject_title}」 {link.subject_kind === "CRITERION" ? t("followup.criterion") : t("tracePage.requirement")}</small>}
    {checked && <p>{checked}</p>}
    {link.state === "STALE" && <div><Button small icon="tick" onClick={() => setOpen(true)}>{t("workspace.checkAgain")}</Button>
      <small className="muted">{" "}{t("hypothesisLink.staleNotice")}</small></div>}
    {open && <RecheckWork projectId={projectId} link={link} siblings={linkSiblings(link, all)} distribution={distribution} onClose={() => setOpen(false)}/>}
  </Callout>;
}
