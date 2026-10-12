import { t } from "../i18n/locale";
import { Button, Callout } from "@blueprintjs/core";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { draftActionFromTest } from "../api/actionDraft";
import { effectDeclaration } from "./effectDeclaration";
import { EffectDeclarationFields } from "./EffectDeclarationFields";
import type { DiscriminationItem } from "../api/judgmentRecords";
import { standingActionNote } from "./judgmentRecordText";
import { draftReadyLines, draftRefusal } from "./testActionDraftText";

/**
 * "Prepare an action request from this test." A person presses it; no model is asked and nothing is approved.
 * The person may say what the request would do (nothing is ticked for them); the server decides the risk from it, so a request
 * with no declared effect comes back as protected, and the screen shows the answer the rules gave.
 */
export function TestActionDraft({ projectId, hypothesisId, testId, standing }: {
  projectId: string; hypothesisId: string; testId: string;
  /** Where the recorded test results leave this hypothesis; a note is shown when they went against it, and nothing is held back. */
  standing?: DiscriminationItem["standing"];
}) {
  const against = standingActionNote([hypothesisId], [{ hypothesis_id: hypothesisId, standing } as DiscriminationItem]);
  const [chosen, setChosen] = useState<ReadonlySet<string>>(new Set());
  const [complete, setComplete] = useState(false);
  const send = useMutation({ mutationFn: () => draftActionFromTest(projectId, hypothesisId, testId, effectDeclaration(chosen, complete)) });
  const choose = (key: string, on: boolean) => setChosen(previous => { const next = new Set(previous); if (on) next.add(key); else next.delete(key); return next; });
  return <div className="test-action-draft">
    <EffectDeclarationFields chosen={chosen} complete={complete} onChoose={choose} onComplete={setComplete}/>
    {against && <Callout compact intent="warning" role="note" className="standing-note">{against}</Callout>}
    <Button small icon="send-to" loading={send.isPending} onClick={() => send.mutate()}>{t("actionDraft.fromTest")}</Button>
    <small className="muted">{" "}{t("actionDraft.noModel")}</small>
    {send.error && <Callout compact intent="warning" role="alert">{draftRefusal(send.error.message)}</Callout>}
    {send.data && <Callout compact intent="primary" role="status">{draftReadyLines(send.data.action).map((line, index) => <p key={index}>{line}</p>)}</Callout>}
  </div>;
}
