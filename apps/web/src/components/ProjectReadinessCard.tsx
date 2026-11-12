import { message } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
import { sentenceGap } from "../i18n/sentences";
import { Button, Tag } from "@blueprintjs/core";
import { projectCutoffText } from "../api/sourceTime";
import { blockedReason } from "./readinessText";

export type ReadinessFacts = {
  /** Number of connected sources, or null when the list has not been read. */
  sourceCount: number | null;
  /** Server-reported model connection; undefined when the readiness read did not say. */
  modelConnected?: boolean;
  /** Workspace internet consent: "GRANTED", "DENIED", "UNDECIDED" or undefined when hosted. */
  consent?: string;
  cutoffAt?: string | null;
  executionReady: boolean;
  hosted: boolean;
};

type Fact = { text: string; intent: "none" | "success" | "warning" };

function facts({ sourceCount, modelConnected, consent, cutoffAt, hosted }: ReadinessFacts): Fact[] {
  const list: Fact[] = [];
  if (sourceCount !== null) list.push({ text: message("readinessSources", sourceCount), intent: sourceCount > 0 ? "success" : "none" });
  if (!hosted) {
    if (modelConnected === true) list.push({ text: t("readiness.modelConnected"), intent: "success" });
    else if (modelConnected === false) list.push({ text: t("readiness.modelMissing"), intent: "warning" });
    if (consent === "UNDECIDED") list.push({ text: t("readiness.consentUndecided"), intent: "warning" });
    else if (consent === "DENIED") list.push({ text: t("readiness.consentOff"), intent: "none" });
    else if (consent === "GRANTED") list.push({ text: t("readiness.consentOn"), intent: "none" });
  }
  const cutoff = projectCutoffText(cutoffAt ?? null);
  if (cutoff) list.push({ text: message("cutoff", cutoff), intent: "none" });
  return list;
}

/**
 * What this project offers before the first question: connected sources, model connection, internet consent and the
 * cutoff, as text tags from values the shell already reads, with one action to the setting that blocks a new run.
 */
export function ProjectReadinessCard({ facts: input, onOpenSettings, onManageSources }: {
  facts: ReadinessFacts; onOpenSettings?: (target?: "consent") => void; onManageSources?: () => void;
}) {
  const list = facts(input);
  const consentMissing = !input.hosted && input.consent === "UNDECIDED";
  const blocked = !input.hosted && !input.executionReady;
  return <section className="project-readiness" aria-label={t("readiness.title")}>
    <p className="readiness-title">{t("readiness.title")}</p>
    {list.length > 0 && <div className="readiness-facts">{list.map(fact => <Tag key={fact.text} minimal intent={fact.intent}>{fact.text}</Tag>)}</div>}
    {blocked
      ? <p className="muted">{blockedReason(consentMissing, input.modelConnected)}{sentenceGap()}{t("composer.savedResearch")}</p>
      : <p className="muted">{t("readiness.readyNotice")}</p>}
    <div className="readiness-actions">
      {blocked && onOpenSettings && (consentMissing && input.modelConnected !== false
        ? <Button small intent="primary" onClick={() => onOpenSettings("consent")}>{t("composer.chooseConsent")}</Button>
        : <Button small intent="primary" onClick={() => onOpenSettings()}>{t("composer.checkModel")}</Button>)}
      {onManageSources && <Button small minimal icon="folder-open" onClick={onManageSources}>{t("workspace.manageSources")}</Button>}
    </div>
  </section>;
}
