import { createContext } from "react";

/**
 * The question the open conversation is answering now (first question and follow-ups, as the server
 * joined them). A hypothesis whose "observation" is exactly this text recorded the question, not an
 * observation, and the hypothesis card leaves that line out.
 */
export const AskedQuestionContext = createContext<string | null>(null);

export function askedQuestion(status: { request?: { effective_question?: string } | null } | undefined): string | null {
  return status?.request?.effective_question ?? null;
}

/** True only when the two texts are the same character for character (surrounding blanks aside). */
export function isTheQuestion(observed: string, asked: string | null): boolean {
  return asked !== null && observed.trim() !== "" && observed.trim() === asked.trim();
}
