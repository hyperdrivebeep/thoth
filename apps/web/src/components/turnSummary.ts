import { t } from "../i18n/locale";
import type { ConversationTurn } from "../api/conversation";
import { textValue } from "../api/presentation";
import { currentnessLabel, currentnessReasonNote } from "./history/historyPresentation";
import { interruptionView } from "./resultInterruption";
import { splitAnswerLead } from "./answerText";
import { answerStatusLabel } from "./statusLabels";

export type TurnTag = { text: string; intent: "none" | "warning" | "danger" | "success" };

/** What a folded turn says about itself: the same state the full result card would show, in a few words. */
export type TurnSummary = {
  tags: TurnTag[];
  note: string | null;
  preview: string | null;
  hasAnswer: boolean;
};

const PREVIEW_CHARS = 160;

/** The first prose line of the answer (no hold lead, no heading line), cut to a preview; null when there is no answer text. */
function previewOf(turn: ConversationTurn): string | null {
  const answer = textValue(turn.result?.answer).trim();
  if (!answer) return null;
  const lines = splitAnswerLead(answer)
    .body.split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");
  const body =
    lines.find((line) => !/^#{1,4}\s/.test(line)) ?? lines[0]?.replace(/^#{1,4}\s+/, "") ?? "";
  const plain = body.replace(/\*\*/g, "").replace(/\s*\(?span:[0-9A-Za-z_-]+\)?/g, "");
  return plain.length > PREVIEW_CHARS ? plain.slice(0, PREVIEW_CHARS) + "…" : plain;
}

/**
 * Reads the folded line's state from the same facts the result card reads (operation state, failure, terminal reason,
 * currentness), so a folded turn and its opened card never disagree.
 */
export function turnSummary(turn: ConversationTurn): TurnSummary {
  const tags: TurnTag[] = [];
  let note: string | null = null;
  if (turn.unavailable)
    return {
      tags: [{ text: t("timeline.turnUnavailable"), intent: "warning" }],
      note: turn.unavailable,
      preview: null,
      hasAnswer: false,
    };
  const view = interruptionView({
    state: turn.state,
    result: turn.result,
    error: turn.error,
    failure: turn.failure,
    terminalReason: turn.terminalReason,
  });
  const preview = previewOf(turn);
  if (view.cancelled) tags.push({ text: t("timeline.turnCancelled"), intent: "danger" });
  else if (view.failed) tags.push({ text: t("timeline.turnFailed"), intent: "danger" });
  else if (view.interrupted)
    tags.push({
      text: view.cutOffKind
        ? `${t("timeline.turnCutOff")} · ${t(`result.${view.cutOffKind}`)}`
        : t("timeline.turnInterrupted"),
      intent: "danger",
    });
  if (preview) {
    const status =
      typeof turn.result?.answer_status === "string"
        ? answerStatusLabel(turn.result.answer_status)
        : null;
    tags.push({ text: status ?? t("timeline.turnAnswered"), intent: "none" });
  } else if (!view.cancelled && !view.failed && !view.interrupted)
    tags.push({ text: t("timeline.turnNoAnswer"), intent: "none" });
  if (turn.currentness && turn.currentness.state !== "CURRENT") {
    tags.push({ text: currentnessLabel(turn.currentness), intent: "warning" });
    note = currentnessReasonNote(turn.currentness);
  } else if (turn.state === "STALE")
    tags.push({ text: t("timeline.turnStale"), intent: "warning" });
  return { tags, note, preview, hasAnswer: Boolean(preview) };
}
