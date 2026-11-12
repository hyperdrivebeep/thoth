import type { CompletedStage, ResearchStatus } from "../api/research";
import { clip, compactPayload, type WorkFacts, type ProgressScene, type TimelineIcon } from "./progressKo";

export const ACTION_COPY: Record<string, {label: string; icon: TimelineIcon}> = {
  stage_completed: {label: "段階が完了", icon: "tick-circle"}, source_read: {label: "資料の位置", icon: "search"},
  source_attached: {label: "接続済みの資料", icon: "document-open"}, model_call: {label: "モデル呼び出し", icon: "cloud"},
  hypothesis_review: {label: "仮説のレビュー", icon: "form"},
};
export function formatDuration(ms: number | null | undefined): string | null {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return null;
  if (ms < 1000) return `${Math.round(ms)} ミリ秒`;
  const seconds = Math.round(ms / 1000), minutes = Math.floor(seconds / 60);
  return seconds < 60 ? `${seconds} 秒` : seconds % 60 ? `${minutes} 分 ${seconds % 60} 秒` : `${minutes} 分`;
}
export function formatBytes(bytes: number | null | undefined): string | null {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return null;
  if (bytes < 1024) return `コンテキスト ${bytes.toLocaleString("ja-JP")}B`;
  const kb = bytes / 1024, mb = kb / 1024;
  return kb < 1024 ? `コンテキスト ${kb < 10 ? kb.toFixed(1) : Math.round(kb)}KB` : `コンテキスト ${mb < 10 ? mb.toFixed(1) : Math.round(mb)}MB`;
}
// A name is said once, however many times it was recorded (several ranges of a source can share one place label).
const join = (values: string[], limit: number) => [...new Set(values)].slice(0, limit).join("、");
const quoted = (values: string[], limit: number) => values.slice(0, limit).map(value => `「${clip(value, 28)}」`).join("、");

export function sceneFor(key: string | undefined, facts: WorkFacts, live = false): ProgressScene {
  const focus = facts.focus, constraints = join(facts.constraints, 3), locators = join(facts.locators, 3);
  const hypotheses = quoted(facts.hypotheses, 3), decided = quoted(facts.decidedHypotheses, 2), sources = join(facts.sources, 2);
  const title = (done: string, doing: string) => live ? doing : done;
  const scenes: Record<string, () => ProgressScene> = {
    RESEARCH_PLANNER: () => ({title: title(`制約を確定しました：${constraints || focus || "質問が求める条件"}`, `制約を確定しています：${constraints || focus || "質問が求める条件"}`), detail: "回答はまだ書かれていません。"}),
    EVIDENCE_RERANKER: () => ({title: title(`${locators || focus || "質問"}に関連する箇所を並べ替えました${sources ? `（${sources}から）` : ""}`, `${locators || focus || "質問"}に関連する箇所を並べ替えています`), detail: "並べたのは関連性だけで、裏づけになるかはまだ判定していません。"}),
    SEMANTIC_REVIEWER: () => ({title: title(`${facts.constraints.find(item => /조건/u.test(item)) || focus || "質問の条件"}を${locators || "元の資料"}と比べました`, `${facts.constraints.find(item => /조건/u.test(item)) || focus || "質問の条件"}を${locators || "元の資料"}と比べています`), detail: "関連しているだけでは、裏づけにはなりません。"}),
    REVIEW_ADJUDICATOR: () => ({title: title(`${focus || "各条件"}の抜けと食い違いを判定しました`, `${focus || "各条件"}の抜けと食い違いを判定しています`), detail: "曖昧な項目は保留のままです。"}),
    SOURCE_PLANNER: () => ({title: title(sources ? `${sources}のほかに、許可されたアドレスをさらに選びました` : "許可されたサイトから、さらにアドレスを選びました", sources ? `${sources}のほかに、許可されたアドレスをさらに選んでいます` : "許可されたサイトから、さらにアドレスを選んでいます"), detail: "これはインターネット全体の検索ではありません。"}),
    HYPOTHESIS_GENERATOR: () => ({title: title(`${hypotheses || `${focus || "質問"}について考えられる説明`}を用意しました`, `${hypotheses || `${focus || "質問"}について考えられる説明`}を用意しています`), detail: "これらはまだ採用されていません。"}),
    HYPOTHESIS_REVIEWER: () => ({title: title(`${decided || hypotheses || "提案された説明"}を元の資料と比べました`, `${decided || hypotheses || "提案された説明"}を元の資料と比べています`), detail: "提案された説明のすべてに判断が出た時点で、レビューが完了します。"}),
    ACTION_PLANNER: () => ({title: title(`${focus ? `${focus}のための` : ""}次のチェックを選びました`, `${focus ? `${focus}のための` : ""}次のチェックを選んでいます`), detail: "これは実行前の下書きです。"}),
    EVIDENCE_EXTRACTOR: () => ({title: title(locators ? `${locators}の箇所を選びました` : `${focus || "表と資料の箇所"}を特定しました`, `${locators || focus || "表と資料の箇所"}を特定しています`), detail: "実際に使うセルと文を探しています。"}),
    SUFFICIENCY_EXPLAINER: () => ({title: title(`現在の根拠で${focus || "質問"}に答えられるかを説明しました`, `現在の根拠で${focus || "質問"}に答えられるかを説明しています`), detail: "不足している点は、回答を保留にしている理由として記録されます。"}),
    COUNTEREVIDENCE_CHALLENGER: () => ({title: title(`${hypotheses || focus || "質問"}に反する根拠を探しました`, `${hypotheses || focus || "質問"}に反する根拠を探しています`), detail: "レビューは、裏づける箇所だけを見るものではありません。"}),
    USER_EXPLAINER: () => ({title: title(`${focus || "現在の判定"}をやさしい言葉で説明しました`, `${focus || "現在の判定"}をやさしい言葉で説明しています`), detail: "内部のコード名ではなく、質問に沿って説明します。"}),
    REFERENCE_MAPPER: () => ({title: title(`引用を元の位置に対応づけました${locators ? `：${locators}` : ""}`, `引用を元の位置に対応づけています${locators ? `：${locators}` : ""}`), detail: "表の番号とセルの位置を、その出どころに結びつけています。"}),
    CONNECTED_SOURCES: () => ({title: `${sources || "このプロジェクトに許可された資料"}だけを開いています`, detail: "これは、ウェブ検索が始まったという意味ではありません。"}),
    SOURCE_SHORTLIST: () => ({title: title(`${sources || "接続済みの資料"}から関連する箇所を選びました`, `${sources || "接続済みの資料"}から関連する箇所を選んでいます`), detail: "時点が未検証の資料は、確認された根拠ではなく、候補のままです。"}),
    HOLD: () => ({title: live ? "モデルを待つあいだに停止しました" : "モデル呼び出しが終わったあとで調査が停止しました", detail: "接続済みの資料は無事で、回答はまだ確定していません。"}),
  };
  const aliases: Record<string, string> = {REQUIREMENTS: "RESEARCH_PLANNER", EVIDENCE_REVIEW: "SEMANTIC_REVIEWER", HYPOTHESES: "HYPOTHESIS_GENERATOR", HYPOTHESIS_REVIEW: "HYPOTHESIS_REVIEWER", ACTION_COMPARISON: "ACTION_PLANNER", EVIDENCE_FOCUS: "EVIDENCE_EXTRACTOR"};
  const render = scenes[aliases[key ?? ""] ?? key ?? ""];
  return render ? render() : {title: title(`${focus || "リクエスト"}に関わる段階を終えました`, `${focus || "リクエスト"}のために資料と条件を確認しています`), detail: "段階の名前だけでは、より具体的な作業は特定できません。"};
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
  if (active.state === "RESERVED") return `モデルの応答を待っています · ${elapsed ?? "経過時間は不明"} · ${first ? `最初のバイト ${first}` : "最初のバイトはまだ届いていません"}`;
  if (typeof received === "number") return `直近のモデルの応答 ${received.toLocaleString("ja-JP")}B${elapsed ? ` · ${elapsed}` : ""}`;
  return elapsed ? `直近のモデル呼び出し · ${elapsed}` : null;
}
export function usageLine(status?: ResearchStatus): string | null {
  const usage = status?.usage; if (!usage) return null;
  return `使用トークン ${usage.total_tokens == null ? "不明" : usage.total_tokens.toLocaleString("ja-JP")}${usage.state === "PARTIAL" ? "（一部のみ観測）" : ""}${usage.cached_input_tokens != null ? ` · キャッシュ入力 ${usage.cached_input_tokens.toLocaleString("ja-JP")}` : ""}`;
}
function evidenceFocusLine(facts: WorkFacts, labels: string[], total: number | null): string | null {
  if (labels.length === 0) return total === 0 || total == null ? `${facts.focus || "質問"}のための表や箇所は、まだ選ばれていません。接続済みのローカルの資料がない場合、この段階ではウェブ検索は始まりません。` : null;
  return `${labels.length} か所を確認しました：${join(labels, 3)}${total != null && total > labels.length ? ` · 全 ${total} か所の一部` : ""}。`;
}
function hypothesisLine(facts: WorkFacts, hypotheses: number, reviews: number | null): string {
  const pending = quoted(facts.pendingHypotheses, 2), decided = quoted(facts.decidedHypotheses, 2);
  if (reviews == null || reviews === 0) return pending ? `まず${pending}を比べます。判断はまだ ${hypotheses} 件足りません。` : `仮説 ${hypotheses} 件 · レビューの判断はまだありません。`;
  if (reviews < hypotheses) return `${decided ? `${decided}をレビュー済みです。` : ""}${pending ? `${pending}：` : `仮説 ${hypotheses} 件のうち ${reviews} 件をレビュー済みです。`}あと ${hypotheses - reviews} 件の判断が残っています。`;
  return decided ? `${decided}まで比べました。` : `仮説 ${hypotheses} 件 · レビュー ${reviews} 件`;
}
const holdReasons: [string, string][] = [
  ["429", "モデル呼び出しが、アカウントの利用上限により拒否されました。"], ["AUTH_REQUIRED", "モデルアカウントの認証が拒否されました。"], ["403", "モデルアカウントの認証が拒否されました。"],
  ["MODEL_NOT_SUPPORTED", "この ChatGPT Codex アカウントは、このモデルに対応していません。"], ["400", "モデルがリクエストを拒否しました。"],
];
function holdLine(status: ResearchStatus): string {
  const reason = status.current_result?.terminal_reason ?? "";
  return holdReasons.find(([code]) => reason.includes(code))?.[1] ?? "モデル呼び出しは、回答が書かれる前に停止しました。";
}
export function missingFor(status: ResearchStatus, facts: WorkFacts, labels: string[], total: number | null, hypotheses: number | null, reviews: number | null): string | null {
  const phase = status.attempt?.phase ?? "";
  if (phase === "EVIDENCE_FOCUS") return evidenceFocusLine(facts, labels, total);
  if ((phase === "HYPOTHESIS_REVIEW" || phase === "HYPOTHESES") && hypotheses != null) return hypothesisLine(facts, hypotheses, reviews);
  if (phase === "CONNECTED_SOURCES") return `${join(facts.sources, 2) || "接続済みの資料"}の一覧を開いています。回答はまだ書かれていません。`;
  if (phase === "SOURCE_SHORTLIST") return `${join(facts.sources, 2) || "接続済みの資料"}から箇所を選んでいます。回答はまだ書かれていません。`;
  return phase === "HOLD" ? holdLine(status) : null;
}
export function stageActionPayload(stage: CompletedStage): string | null {
  const count = Array.isArray(stage.dispatch_ids) ? stage.dispatch_ids.length : null;
  return compactPayload([formatDuration(stage.elapsed_ms), formatBytes(stage.context_bytes), count != null && count > 0 ? `モデル呼び出し ${count} 回` : null]);
}
export function modelActionPayload(status?: ResearchStatus): { payload: string | null; live: boolean } | null {
  const active = dispatch(status); if (!active) return null;
  const observation = active.transport_observation, received = observation?.received_bytes ?? active.received_bytes;
  const elapsed = formatDuration(observation?.elapsed_ms), first = formatDuration(observation?.first_byte_ms ?? observation?.first_response_ms);
  return active.state === "RESERVED" ? {live: true, payload: compactPayload([elapsed ?? "経過時間は不明", first ? `最初のバイト ${first}` : "最初のバイトはまだ届いていません"])}
    : {live: false, payload: compactPayload([typeof received === "number" ? `${received.toLocaleString("ja-JP")}B` : null, elapsed])};
}
export function hypothesisActionPayload(hypotheses: number | null, reviews: number | null): string | null {
  if (hypotheses == null) return null;
  if (reviews == null) return `仮説 ${hypotheses} 件 · 判断はまだありません`;
  if (reviews < hypotheses) return `仮説 ${hypotheses} 件のうち ${reviews} 件に判断があります`;
  return `仮説 ${hypotheses} 件 · レビュー ${reviews} 件`;
}