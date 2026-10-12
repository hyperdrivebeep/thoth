import type { CompletedStage, ResearchStatus } from "../api/research";
import { clip, compactPayload, type WorkFacts, type ProgressScene, type TimelineIcon } from "./progressKo";

export const ACTION_COPY: Record<string, {label: string; icon: TimelineIcon}> = {
  stage_completed: {label: "Stage complete", icon: "tick-circle"}, source_read: {label: "Source location", icon: "search"},
  source_attached: {label: "Connected source", icon: "document-open"}, model_call: {label: "Model call", icon: "cloud"},
  hypothesis_review: {label: "Hypothesis review", icon: "form"},
};
export function formatDuration(ms: number | null | undefined): string | null {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return null;
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = Math.round(ms / 1000), minutes = Math.floor(seconds / 60);
  return seconds < 60 ? `${seconds}s` : seconds % 60 ? `${minutes}m ${seconds % 60}s` : `${minutes}m`;
}
export function formatBytes(bytes: number | null | undefined): string | null {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return null;
  if (bytes < 1024) return `Context ${bytes.toLocaleString("en-US")}B`;
  const kb = bytes / 1024, mb = kb / 1024;
  return kb < 1024 ? `Context ${kb < 10 ? kb.toFixed(1) : Math.round(kb)}KB` : `Context ${mb < 10 ? mb.toFixed(1) : Math.round(mb)}MB`;
}
const join = (values: string[], limit: number) => values.slice(0, limit).join(", ");
const quoted = (values: string[], limit: number) => values.slice(0, limit).map(value => `“${clip(value, 28)}”`).join(", ");

export function sceneFor(key: string | undefined, facts: WorkFacts, live = false): ProgressScene {
  const focus = facts.focus, constraints = join(facts.constraints, 3), locators = join(facts.locators, 3);
  const hypotheses = quoted(facts.hypotheses, 3), decided = quoted(facts.decidedHypotheses, 2), sources = join(facts.sources, 2);
  const title = (done: string, doing: string) => live ? doing : done;
  const scenes: Record<string, () => ProgressScene> = {
    RESEARCH_PLANNER: () => ({title: title(`Fixed the constraints: ${constraints || focus || "the question's required conditions"}`, `Fixing the constraints: ${constraints || focus || "the question's required conditions"}`), detail: "The answer has not been written yet."}),
    EVIDENCE_RERANKER: () => ({title: title(`Reordered passages relevant to ${locators || focus || "the question"}${sources ? ` from ${sources}` : ""}`, `Reordering passages relevant to ${locators || focus || "the question"}`), detail: "Only relevance was ranked; support has not been assessed yet."}),
    SEMANTIC_REVIEWER: () => ({title: title(`Compared ${facts.constraints.find(item => /조건/u.test(item)) || focus || "the question's conditions"} with ${locators || "the original sources"}`, `Comparing ${facts.constraints.find(item => /조건/u.test(item)) || focus || "the question's conditions"} with ${locators || "the original sources"}`), detail: "Relevance alone does not establish support."}),
    REVIEW_ADJUDICATOR: () => ({title: title(`Assessed omissions and conflicts in ${focus || "the conditions"}`, `Assessing omissions and conflicts in ${focus || "the conditions"}`), detail: "Ambiguous items remain on hold."}),
    SOURCE_PLANNER: () => ({title: title(sources ? `Selected further permitted addresses beyond ${sources}` : "Selected further addresses on permitted sites", sources ? `Selecting further permitted addresses beyond ${sources}` : "Selecting further addresses on permitted sites"), detail: "This is not a search of the entire internet."}),
    HYPOTHESIS_GENERATOR: () => ({title: title(`Prepared ${hypotheses || `possible explanations for ${focus || "the question"}`}`, `Preparing ${hypotheses || `possible explanations for ${focus || "the question"}`}`), detail: "These have not been adopted."}),
    HYPOTHESIS_REVIEWER: () => ({title: title(`Compared ${decided || hypotheses || "the proposed explanations"} with the original sources`, `Comparing ${decided || hypotheses || "the proposed explanations"} with the original sources`), detail: "Review is complete only when every proposed explanation has a decision."}),
    ACTION_PLANNER: () => ({title: title(`Selected the next checks${focus ? ` for ${focus}` : ""}`, `Selecting the next checks${focus ? ` for ${focus}` : ""}`), detail: "This is a draft before execution."}),
    EVIDENCE_EXTRACTOR: () => ({title: title(locators ? `Selected passages at ${locators}` : `Located ${focus || "tables and source passages"}`, `Locating ${locators || focus || "tables and source passages"}`), detail: "Finding the actual cells and sentences to use."}),
    SUFFICIENCY_EXPLAINER: () => ({title: title(`Explained whether current evidence can answer ${focus || "the question"}`, `Explaining whether current evidence can answer ${focus || "the question"}`), detail: "Missing items are recorded as reasons for holding the answer."}),
    COUNTEREVIDENCE_CHALLENGER: () => ({title: title(`Sought evidence against ${hypotheses || focus || "the question"}`, `Seeking evidence against ${hypotheses || focus || "the question"}`), detail: "The review considers more than supporting passages."}),
    USER_EXPLAINER: () => ({title: title(`Explained ${focus || "the current assessment"} in plain language`, `Explaining ${focus || "the current assessment"} in plain language`), detail: "The explanation follows the question rather than internal code names."}),
    REFERENCE_MAPPER: () => ({title: title(`Matched citations to original locations${locators ? `: ${locators}` : ""}`, `Matching citations to original locations${locators ? `: ${locators}` : ""}`), detail: "Linking table numbers and cell locations to their sources."}),
    CONNECTED_SOURCES: () => ({title: `Opening only ${sources || "sources authorized for this project"}`, detail: "This does not mean a web search has started."}),
    SOURCE_SHORTLIST: () => ({title: title(`Selected relevant passages from ${sources || "connected sources"}`, `Selecting relevant passages from ${sources || "connected sources"}`), detail: "Sources with unverified timing remain candidates, not confirmed evidence."}),
    HOLD: () => ({title: live ? "Stopped while waiting for the model" : "Research stopped after the model call ended", detail: "Connected sources remain intact; the answer has not been finalized."}),
  };
  const aliases: Record<string, string> = {REQUIREMENTS: "RESEARCH_PLANNER", EVIDENCE_REVIEW: "SEMANTIC_REVIEWER", HYPOTHESES: "HYPOTHESIS_GENERATOR", HYPOTHESIS_REVIEW: "HYPOTHESIS_REVIEWER", ACTION_COMPARISON: "ACTION_PLANNER", EVIDENCE_FOCUS: "EVIDENCE_EXTRACTOR"};
  const render = scenes[aliases[key ?? ""] ?? key ?? ""];
  return render ? render() : {title: title(`Finished a stage related to ${focus || "the request"}`, `Checking sources and conditions for ${focus || "the request"}`), detail: "The stage name alone does not identify a more specific task."};
}

function dispatch(status?: ResearchStatus) {
  const values = status?.model_dispatches ?? [];
  return [...values].reverse().find(item => item.state === "RESERVED") ?? values.at(-1);
}
export function waitLine(status?: ResearchStatus): string | null {
  const active = dispatch(status); if (!active) return null;
  const observation = active.transport_observation;
  const elapsed = formatDuration(observation?.elapsed_ms), first = formatDuration(observation?.first_byte_ms ?? observation?.first_response_ms);
  const received = observation?.received_bytes ?? active.received_bytes;
  if (active.state === "RESERVED") return `Waiting for model response · ${elapsed ?? "elapsed time unknown"} · ${first ? `first byte ${first}` : "no first byte yet"}`;
  if (typeof received === "number") return `Last model response ${received.toLocaleString("en-US")}B${elapsed ? ` · ${elapsed}` : ""}`;
  return elapsed ? `Last model call · ${elapsed}` : null;
}
export function usageLine(status?: ResearchStatus): string | null {
  const usage = status?.usage; if (!usage) return null;
  return `Tokens used ${usage.total_tokens == null ? "unknown" : usage.total_tokens.toLocaleString("en-US")}${usage.state === "PARTIAL" ? " (partially observed)" : ""}${usage.cached_input_tokens != null ? ` · Cached input ${usage.cached_input_tokens.toLocaleString("en-US")}` : ""}`;
}
export function missingFor(status: ResearchStatus, facts: WorkFacts, labels: string[], total: number | null, hypotheses: number | null, reviews: number | null): string | null {
  const phase = status.attempt?.phase ?? "";
  if (phase === "EVIDENCE_FOCUS" && labels.length === 0 && (total === 0 || total == null)) return `No table or passage has yet been selected for ${facts.focus || "the question"}. Without a connected local source, web search does not start at this stage.`;
  if (phase === "EVIDENCE_FOCUS" && labels.length > 0) return `Checked ${labels.length} locations: ${join(labels, 3)}${total != null && total > labels.length ? ` · Part of ${total} locations` : ""}.`;
  if ((phase === "HYPOTHESIS_REVIEW" || phase === "HYPOTHESES") && hypotheses != null) {
    const pending = quoted(facts.pendingHypotheses, 2), decided = quoted(facts.decidedHypotheses, 2);
    if (reviews == null || reviews === 0) return pending ? `Will compare ${pending} first. ${hypotheses} decisions are still missing.` : `${hypotheses} hypotheses · No review decisions yet.`;
    if (reviews < hypotheses) return `${decided ? `Reviewed ${decided}. ` : ""}${pending ? `${pending}: ` : `${reviews} of ${hypotheses} hypotheses reviewed. `}${hypotheses - reviews} decisions remain to be completed.`;
    return decided ? `Compared through ${decided}.` : `${hypotheses} hypotheses · ${reviews} reviews`;
  }
  if (phase === "CONNECTED_SOURCES") return `Opening the list of ${join(facts.sources, 2) || "connected sources"}; the answer has not been written yet.`;
  if (phase === "SOURCE_SHORTLIST") return `Selecting passages from ${join(facts.sources, 2) || "connected sources"}; the answer has not been written yet.`;
  if (phase === "HOLD") {
    const reason = status.current_result?.terminal_reason ?? "";
    if (reason.includes("429")) return "The model call was rejected by the account usage limit.";
    if (reason.includes("AUTH_REQUIRED") || reason.includes("403")) return "Model account authentication was rejected.";
    if (reason.includes("MODEL_NOT_SUPPORTED")) return "This ChatGPT Codex account does not support the model.";
    if (reason.includes("400")) return "The model rejected the request.";
    return "The model call stopped before writing the answer.";
  }
  return null;
}
export function stageActionPayload(stage: CompletedStage): string | null {
  const count = Array.isArray(stage.dispatch_ids) ? stage.dispatch_ids.length : null;
  return compactPayload([formatDuration(stage.elapsed_ms), formatBytes(stage.context_bytes), count != null && count > 0 ? `${count} model ${count === 1 ? "call" : "calls"}` : null]);
}
export function modelActionPayload(status?: ResearchStatus): { payload: string | null; live: boolean } | null {
  const active = dispatch(status); if (!active) return null;
  const observation = active.transport_observation, received = observation?.received_bytes ?? active.received_bytes;
  const elapsed = formatDuration(observation?.elapsed_ms), first = formatDuration(observation?.first_byte_ms ?? observation?.first_response_ms);
  return active.state === "RESERVED" ? {live: true, payload: compactPayload([elapsed ?? "Elapsed time unknown", first ? `First byte ${first}` : "No first byte yet"])}
    : {live: false, payload: compactPayload([typeof received === "number" ? `${received.toLocaleString("en-US")}B` : null, elapsed])};
}
export function hypothesisActionPayload(hypotheses: number | null, reviews: number | null): string | null {
  if (hypotheses == null) return null;
  if (reviews == null) return `${hypotheses} hypotheses · No decisions yet`;
  if (reviews < hypotheses) return `${reviews} of ${hypotheses} hypotheses have decisions`;
  return `${hypotheses} hypotheses · ${reviews} reviews`;
}
