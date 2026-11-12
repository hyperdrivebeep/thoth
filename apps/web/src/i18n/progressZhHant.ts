import type { CompletedStage, ResearchStatus } from "../api/research";
import { clip, compactPayload, type WorkFacts, type ProgressScene, type TimelineIcon } from "./progressKo";

export const ACTION_COPY: Record<string, {label: string; icon: TimelineIcon}> = {
  stage_completed: {label: "階段完成", icon: "tick-circle"}, source_read: {label: "資料位置", icon: "search"},
  source_attached: {label: "已連線的資料", icon: "document-open"}, model_call: {label: "模型呼叫", icon: "cloud"},
  hypothesis_review: {label: "假設審查", icon: "form"},
};
export function formatDuration(ms: number | null | undefined): string | null {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return null;
  if (ms < 1000) return `${Math.round(ms)} 毫秒`;
  const seconds = Math.round(ms / 1000), minutes = Math.floor(seconds / 60);
  return seconds < 60 ? `${seconds} 秒` : seconds % 60 ? `${minutes} 分 ${seconds % 60} 秒` : `${minutes} 分鐘`;
}
export function formatBytes(bytes: number | null | undefined): string | null {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return null;
  if (bytes < 1024) return `上下文 ${bytes.toLocaleString("zh-TW")}B`;
  const kb = bytes / 1024, mb = kb / 1024;
  return kb < 1024 ? `上下文 ${kb < 10 ? kb.toFixed(1) : Math.round(kb)}KB` : `上下文 ${mb < 10 ? mb.toFixed(1) : Math.round(mb)}MB`;
}
// A name is said once, however many times it was recorded (several ranges of a source can share one place label).
const join = (values: string[], limit: number) => [...new Set(values)].slice(0, limit).join("、");
const quoted = (values: string[], limit: number) => values.slice(0, limit).map(value => `“${clip(value, 28)}”`).join("、");

export function sceneFor(key: string | undefined, facts: WorkFacts, live = false): ProgressScene {
  const focus = facts.focus, constraints = join(facts.constraints, 3), locators = join(facts.locators, 3);
  const hypotheses = quoted(facts.hypotheses, 3), decided = quoted(facts.decidedHypotheses, 2), sources = join(facts.sources, 2);
  const title = (done: string, doing: string) => live ? doing : done;
  const scenes: Record<string, () => ProgressScene> = {
    RESEARCH_PLANNER: () => ({title: title(`已確定約束：${constraints || focus || "問題所要求的條件"}`, `正在確定約束：${constraints || focus || "問題所要求的條件"}`), detail: "回答尚未撰寫。"}),
    EVIDENCE_RERANKER: () => ({title: title(`已重新排序與${locators || focus || "問題"}相關的段落${sources ? `（來自${sources}）` : ""}`, `正在重新排序與${locators || focus || "問題"}相關的段落`), detail: "只排列了相關性，尚未評估是否支持。"}),
    SEMANTIC_REVIEWER: () => ({title: title(`已將${facts.constraints.find(item => /조건/u.test(item)) || focus || "問題的條件"}與${locators || "原始資料"}進行比較`, `正在將${facts.constraints.find(item => /조건/u.test(item)) || focus || "問題的條件"}與${locators || "原始資料"}進行比較`), detail: "僅有相關性並不能證明得到支持。"}),
    REVIEW_ADJUDICATOR: () => ({title: title(`已評估${focus || "各條件"}中的遺漏和衝突`, `正在評估${focus || "各條件"}中的遺漏和衝突`), detail: "含糊的項目仍處於暫緩狀態。"}),
    SOURCE_PLANNER: () => ({title: title(sources ? `已在${sources}之外選出更多允許的網址` : "已在允許的網站上選出更多網址", sources ? `正在${sources}之外選擇更多允許的網址` : "正在允許的網站上選擇更多網址"), detail: "這不是對整個網際網路的搜尋。"}),
    HYPOTHESIS_GENERATOR: () => ({title: title(`已準備${hypotheses || `${focus || "問題"}的可能解釋`}`, `正在準備${hypotheses || `${focus || "問題"}的可能解釋`}`), detail: "這些尚未被採納。"}),
    HYPOTHESIS_REVIEWER: () => ({title: title(`已將${decided || hypotheses || "提出的解釋"}與原始資料進行比較`, `正在將${decided || hypotheses || "提出的解釋"}與原始資料進行比較`), detail: "只有每個提出的解釋都有了決定，審查才算完成。"}),
    ACTION_PLANNER: () => ({title: title(`已選出${focus ? `${focus}的` : ""}後續檢查`, `正在選擇${focus ? `${focus}的` : ""}後續檢查`), detail: "這是執行之前的草稿。"}),
    EVIDENCE_EXTRACTOR: () => ({title: title(locators ? `已選出${locators}處的段落` : `已定位${focus || "表格和資料段落"}`, `正在定位${locators || focus || "表格和資料段落"}`), detail: "正在找出實際要用的單元格和句子。"}),
    SUFFICIENCY_EXPLAINER: () => ({title: title(`已說明現有證據能否回答${focus || "問題"}`, `正在說明現有證據能否回答${focus || "問題"}`), detail: "缺少的內容會被記錄為回答暫緩的原因。"}),
    COUNTEREVIDENCE_CHALLENGER: () => ({title: title(`已尋找不利於${hypotheses || focus || "問題"}的證據`, `正在尋找不利於${hypotheses || focus || "問題"}的證據`), detail: "審查考慮的不只是支持性的段落。"}),
    USER_EXPLAINER: () => ({title: title(`已用通俗的語言說明${focus || "目前的評估"}`, `正在用通俗的語言說明${focus || "目前的評估"}`), detail: "說明圍繞問題展開，而不是使用內部程式碼名稱。"}),
    REFERENCE_MAPPER: () => ({title: title(`已將引用與原始位置對應${locators ? `：${locators}` : ""}`, `正在將引用與原始位置對應${locators ? `：${locators}` : ""}`), detail: "正在把表格編號和單元格位置連結到其來源。"}),
    CONNECTED_SOURCES: () => ({title: `只開啟${sources || "已授權給此專案的資料"}`, detail: "這並不表示網路搜尋已經開始。"}),
    SOURCE_SHORTLIST: () => ({title: title(`已從${sources || "已連線的資料"}中選出相關段落`, `正在從${sources || "已連線的資料"}中選擇相關段落`), detail: "時間未驗證的資料仍是候選，而不是已確認的證據。"}),
    HOLD: () => ({title: live ? "等待模型時已停止" : "模型呼叫結束後研究已停止", detail: "已連線的資料保持完好；回答尚未定稿。"}),
  };
  const aliases: Record<string, string> = {REQUIREMENTS: "RESEARCH_PLANNER", EVIDENCE_REVIEW: "SEMANTIC_REVIEWER", HYPOTHESES: "HYPOTHESIS_GENERATOR", HYPOTHESIS_REVIEW: "HYPOTHESIS_REVIEWER", ACTION_COMPARISON: "ACTION_PLANNER", EVIDENCE_FOCUS: "EVIDENCE_EXTRACTOR"};
  const render = scenes[aliases[key ?? ""] ?? key ?? ""];
  return render ? render() : {title: title(`已完成與${focus || "請求"}相關的一個階段`, `正在為${focus || "請求"}檢查資料和條件`), detail: "僅憑階段名稱無法確定更具體的任務。"};
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
  if (active.state === "RESERVED") return `正在等待模型回應 · ${elapsed ?? "耗時未知"} · ${first ? `首位元組 ${first}` : "尚未收到首位元組"}`;
  if (typeof received === "number") return `上次模型回應 ${received.toLocaleString("zh-TW")}B${elapsed ? ` · ${elapsed}` : ""}`;
  return elapsed ? `上次模型呼叫 · ${elapsed}` : null;
}
export function usageLine(status?: ResearchStatus): string | null {
  const usage = status?.usage; if (!usage) return null;
  return `已用 token ${usage.total_tokens == null ? "未知" : usage.total_tokens.toLocaleString("zh-TW")}${usage.state === "PARTIAL" ? "（部分觀察到）" : ""}${usage.cached_input_tokens != null ? ` · 快取輸入 ${usage.cached_input_tokens.toLocaleString("zh-TW")}` : ""}`;
}
function evidenceFocusLine(facts: WorkFacts, labels: string[], total: number | null): string | null {
  if (labels.length === 0) return total === 0 || total == null ? `尚未為${facts.focus || "問題"}選出任何表格或段落。沒有已連線的本地資料時，此階段不會開始網路搜尋。` : null;
  return `已檢查 ${labels.length} 處位置：${join(labels, 3)}${total != null && total > labels.length ? ` · 共 ${total} 處位置中的一部分` : ""}。`;
}
function hypothesisLine(facts: WorkFacts, hypotheses: number, reviews: number | null): string {
  const pending = quoted(facts.pendingHypotheses, 2), decided = quoted(facts.decidedHypotheses, 2);
  if (reviews == null || reviews === 0) return pending ? `將先比較${pending}。還缺少 ${hypotheses} 項決定。` : `${hypotheses} 個假設 · 尚無審查決定。`;
  if (reviews < hypotheses) return `${decided ? `已審查${decided}。` : ""}${pending ? `${pending}：` : `已審查 ${hypotheses} 個假設中的 ${reviews} 個。`}還有 ${hypotheses - reviews} 項決定有待完成。`;
  return decided ? `已比較到${decided}。` : `${hypotheses} 個假設 · ${reviews} 項審查`;
}
const holdReasons: [string, string][] = [
  ["429", "模型呼叫被帳戶的使用上限拒絕。"], ["AUTH_REQUIRED", "模型帳戶的身分驗證被拒絕。"], ["403", "模型帳戶的身分驗證被拒絕。"],
  ["MODEL_NOT_SUPPORTED", "此 ChatGPT Codex 帳戶不支援該模型。"], ["400", "模型拒絕了該請求。"],
];
function holdLine(status: ResearchStatus): string {
  const reason = status.current_result?.terminal_reason ?? "";
  return holdReasons.find(([code]) => reason.includes(code))?.[1] ?? "模型呼叫在撰寫回答之前停止了。";
}
export function missingFor(status: ResearchStatus, facts: WorkFacts, labels: string[], total: number | null, hypotheses: number | null, reviews: number | null): string | null {
  const phase = status.attempt?.phase ?? "";
  if (phase === "EVIDENCE_FOCUS") return evidenceFocusLine(facts, labels, total);
  if ((phase === "HYPOTHESIS_REVIEW" || phase === "HYPOTHESES") && hypotheses != null) return hypothesisLine(facts, hypotheses, reviews);
  if (phase === "CONNECTED_SOURCES") return `正在開啟${join(facts.sources, 2) || "已連線的資料"}的列表；回答尚未撰寫。`;
  if (phase === "SOURCE_SHORTLIST") return `正在從${join(facts.sources, 2) || "已連線的資料"}中選擇段落；回答尚未撰寫。`;
  return phase === "HOLD" ? holdLine(status) : null;
}
export function stageActionPayload(stage: CompletedStage): string | null {
  const count = Array.isArray(stage.dispatch_ids) ? stage.dispatch_ids.length : null;
  return compactPayload([formatDuration(stage.elapsed_ms), formatBytes(stage.context_bytes), count != null && count > 0 ? `${count} 次模型呼叫` : null]);
}
export function modelActionPayload(status?: ResearchStatus): { payload: string | null; live: boolean } | null {
  const active = dispatch(status); if (!active) return null;
  const observation = active.transport_observation, received = observation?.received_bytes ?? active.received_bytes;
  const elapsed = formatDuration(observation?.elapsed_ms), first = formatDuration(observation?.first_byte_ms ?? observation?.first_response_ms);
  return active.state === "RESERVED" ? {live: true, payload: compactPayload([elapsed ?? "耗時未知", first ? `首位元組 ${first}` : "尚未收到首位元組"])}
    : {live: false, payload: compactPayload([typeof received === "number" ? `${received.toLocaleString("zh-TW")}B` : null, elapsed])};
}
export function hypothesisActionPayload(hypotheses: number | null, reviews: number | null): string | null {
  if (hypotheses == null) return null;
  if (reviews == null) return `${hypotheses} 個假設 · 尚無決定`;
  if (reviews < hypotheses) return `${hypotheses} 個假設中已有 ${reviews} 個作出決定`;
  return `${hypotheses} 個假設 · ${reviews} 項審查`;
}