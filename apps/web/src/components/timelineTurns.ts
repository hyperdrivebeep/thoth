import type { ConversationPage, ConversationTurn } from "../api/conversation";
import { withCurrentCheckpoint } from "../api/conversation";
import type { ResearchStatus } from "../api/research";
import { interruptionView } from "./resultInterruption";

/** The turns of a thread in request order, each request once, with the live checkpoint applied to the turn it belongs to. */
export function collectTurns(pages: ConversationPage[] | undefined, status: ResearchStatus | undefined): ConversationTurn[] {
  const unique = new Map<string, ConversationTurn>();
  for (const page of pages ?? []) for (const turn of page.turns) unique.set(turn.input.request_revision_digest, turn);
  return withCurrentCheckpoint([...unique.values()].sort((a, b) => a.input.request_epoch - b.input.request_epoch), status);
}

/** Requests that came after an earlier saved answer: their cards may say "a saved answer exists" when they fail. */
export function savedBeforeSet(turns: ConversationTurn[]): Set<string> {
  const savedBefore = new Set<string>();
  let saved = false;
  for (const turn of turns) {
    if (saved) savedBefore.add(turn.input.request_revision_digest);
    if (turn.result && !turn.unavailable) saved = true;
  }
  return savedBefore;
}

/** Counts for the heading line: how many turns, how many carry an answer, how many were cut off, failed or cancelled. */
export function turnTally(turns: ConversationTurn[]): { total: number; answered: number; interrupted: number } {
  let answered = 0, interrupted = 0;
  for (const turn of turns) {
    if (turn.unavailable) continue;
    const view = interruptionView({ state: turn.state, result: turn.result, error: turn.error, failure: turn.failure, terminalReason: turn.terminalReason });
    if (typeof turn.result?.answer === "string" && turn.result.answer.trim()) answered += 1;
    if (view.interrupted) interrupted += 1;
  }
  return { total: turns.length, answered, interrupted };
}
