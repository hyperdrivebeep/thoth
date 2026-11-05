import type { CompletedStage, ResearchStatus } from "../api/research";
import { clip, compactPayload, type WorkFacts, type ProgressScene, type TimelineIcon } from "./progressKo";

export const ACTION_COPY: Record<string, {label: string; icon: TimelineIcon}> = {
  stage_completed: {label: "阶段完成", icon: "tick-circle"}, source_read: {label: "资料位置", icon: "search"},
  source_attached: {label: "已连接的资料", icon: "document-open"}, model_call: {label: "模型调用", icon: "cloud"},
  hypothesis_review: {label: "假设审查", icon: "form"},
};
export function formatDuration(ms: number | null | undefined): string | null {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return null;
  if (ms < 1000) return `${Math.round(ms)} 毫秒`;
  const seconds = Math.round(ms / 1000), minutes = Math.floor(seconds / 60);
  return seconds < 60 ? `${seconds} 秒` : seconds % 60 ? `${minutes} 分 ${seconds % 60} 秒` : `${minutes} 分钟`;
}
export function formatBytes(bytes: number | null | undefined): string | null {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return null;
  if (bytes < 1024) return `上下文 ${bytes.toLocaleString("zh-CN")}B`;
  const kb = bytes / 1024, mb = kb / 1024;
  return kb < 1024 ? `上下文 ${kb < 10 ? kb.toFixed(1) : Math.round(kb)}KB` : `上下文 ${mb < 10 ? mb.toFixed(1) : Math.round(mb)}MB`;
}
const join = (values: string[], limit: number) => values.slice(0, limit).join("、");
const quoted = (values: string[], limit: number) => values.slice(0, limit).map(value => `“${clip(value, 28)}”`).join("、");

export function sceneFor(key: string | undefined, facts: WorkFacts, live = false): ProgressScene {
  const focus = facts.focus, constraints = join(facts.constraints, 3), locators = join(facts.locators, 3);
  const hypotheses = quoted(facts.hypotheses, 3), decided = quoted(facts.decidedHypotheses, 2), sources = join(facts.sources, 2);
  const title = (done: string, doing: string) => live ? doing : done;
  const scenes: Record<string, () => ProgressScene> = {
    RESEARCH_PLANNER: () => ({title: title(`已确定约束：${constraints || focus || "问题所要求的条件"}`, `正在确定约束：${constraints || focus || "问题所要求的条件"}`), detail: "回答尚未撰写。"}),
    EVIDENCE_RERANKER: () => ({title: title(`已重新排序与${locators || focus || "问题"}相关的段落${sources ? `（来自${sources}）` : ""}`, `正在重新排序与${locators || focus || "问题"}相关的段落`), detail: "只排列了相关性，尚未评估是否支持。"}),
    SEMANTIC_REVIEWER: () => ({title: title(`已将${facts.constraints.find(item => /조건/u.test(item)) || focus || "问题的条件"}与${locators || "原始资料"}进行比较`, `正在将${facts.constraints.find(item => /조건/u.test(item)) || focus || "问题的条件"}与${locators || "原始资料"}进行比较`), detail: "仅有相关性并不能证明得到支持。"}),
    REVIEW_ADJUDICATOR: () => ({title: title(`已评估${focus || "各条件"}中的遗漏和冲突`, `正在评估${focus || "各条件"}中的遗漏和冲突`), detail: "含糊的项目仍处于暂缓状态。"}),
    SOURCE_PLANNER: () => ({title: title(sources ? `已在${sources}之外选出更多允许的地址` : "已在允许的网站上选出更多地址", sources ? `正在${sources}之外选择更多允许的地址` : "正在允许的网站上选择更多地址"), detail: "这不是对整个互联网的搜索。"}),
    HYPOTHESIS_GENERATOR: () => ({title: title(`已准备${hypotheses || `${focus || "问题"}的可能解释`}`, `正在准备${hypotheses || `${focus || "问题"}的可能解释`}`), detail: "这些尚未被采纳。"}),
    HYPOTHESIS_REVIEWER: () => ({title: title(`已将${decided || hypotheses || "提出的解释"}与原始资料进行比较`, `正在将${decided || hypotheses || "提出的解释"}与原始资料进行比较`), detail: "只有每个提出的解释都有了决定，审查才算完成。"}),
    ACTION_PLANNER: () => ({title: title(`已选出${focus ? `${focus}的` : ""}后续检查`, `正在选择${focus ? `${focus}的` : ""}后续检查`), detail: "这是执行之前的草稿。"}),
    EVIDENCE_EXTRACTOR: () => ({title: title(locators ? `已选出${locators}处的段落` : `已定位${focus || "表格和资料段落"}`, `正在定位${locators || focus || "表格和资料段落"}`), detail: "正在找出实际要用的单元格和句子。"}),
    SUFFICIENCY_EXPLAINER: () => ({title: title(`已说明现有证据能否回答${focus || "问题"}`, `正在说明现有证据能否回答${focus || "问题"}`), detail: "缺少的内容会被记录为回答暂缓的原因。"}),
    COUNTEREVIDENCE_CHALLENGER: () => ({title: title(`已寻找不利于${hypotheses || focus || "问题"}的证据`, `正在寻找不利于${hypotheses || focus || "问题"}的证据`), detail: "审查考虑的不只是支持性的段落。"}),
    USER_EXPLAINER: () => ({title: title(`已用通俗的语言说明${focus || "当前的评估"}`, `正在用通俗的语言说明${focus || "当前的评估"}`), detail: "说明围绕问题展开，而不是使用内部代码名称。"}),
    REFERENCE_MAPPER: () => ({title: title(`已将引用与原始位置对应${locators ? `：${locators}` : ""}`, `正在将引用与原始位置对应${locators ? `：${locators}` : ""}`), detail: "正在把表格编号和单元格位置链接到其来源。"}),
    CONNECTED_SOURCES: () => ({title: `只打开${sources || "已授权给此项目的资料"}`, detail: "这并不表示网络搜索已经开始。"}),
    SOURCE_SHORTLIST: () => ({title: title(`已从${sources || "已连接的资料"}中选出相关段落`, `正在从${sources || "已连接的资料"}中选择相关段落`), detail: "时间未验证的资料仍是候选，而不是已确认的证据。"}),
    HOLD: () => ({title: live ? "等待模型时已停止" : "模型调用结束后研究已停止", detail: "已连接的资料保持完好；回答尚未定稿。"}),
  };
  const aliases: Record<string, string> = {REQUIREMENTS: "RESEARCH_PLANNER", EVIDENCE_REVIEW: "SEMANTIC_REVIEWER", HYPOTHESES: "HYPOTHESIS_GENERATOR", HYPOTHESIS_REVIEW: "HYPOTHESIS_REVIEWER", ACTION_COMPARISON: "ACTION_PLANNER", EVIDENCE_FOCUS: "EVIDENCE_EXTRACTOR"};
  const render = scenes[aliases[key ?? ""] ?? key ?? ""];
  return render ? render() : {title: title(`已完成与${focus || "请求"}相关的一个阶段`, `正在为${focus || "请求"}检查资料和条件`), detail: "仅凭阶段名称无法确定更具体的任务。"};
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
  if (active.state === "RESERVED") return `正在等待模型响应 · ${elapsed ?? "耗时未知"} · ${first ? `首字节 ${first}` : "尚未收到首字节"}`;
  if (typeof received === "number") return `上次模型响应 ${received.toLocaleString("zh-CN")}B${elapsed ? ` · ${elapsed}` : ""}`;
  return elapsed ? `上次模型调用 · ${elapsed}` : null;
}
export function usageLine(status?: ResearchStatus): string | null {
  const usage = status?.usage; if (!usage) return null;
  return `已用 token ${usage.total_tokens == null ? "未知" : usage.total_tokens.toLocaleString("zh-CN")}${usage.state === "PARTIAL" ? "（部分观察到）" : ""}${usage.cached_input_tokens != null ? ` · 缓存输入 ${usage.cached_input_tokens.toLocaleString("zh-CN")}` : ""}`;
}
function evidenceFocusLine(facts: WorkFacts, labels: string[], total: number | null): string | null {
  if (labels.length === 0) return total === 0 || total == null ? `尚未为${facts.focus || "问题"}选出任何表格或段落。没有已连接的本地资料时，此阶段不会开始网络搜索。` : null;
  return `已检查 ${labels.length} 处位置：${join(labels, 3)}${total != null && total > labels.length ? ` · 共 ${total} 处位置中的一部分` : ""}。`;
}
function hypothesisLine(facts: WorkFacts, hypotheses: number, reviews: number | null): string {
  const pending = quoted(facts.pendingHypotheses, 2), decided = quoted(facts.decidedHypotheses, 2);
  if (reviews == null || reviews === 0) return pending ? `将先比较${pending}。还缺少 ${hypotheses} 项决定。` : `${hypotheses} 个假设 · 尚无审查决定。`;
  if (reviews < hypotheses) return `${decided ? `已审查${decided}。` : ""}${pending ? `${pending}：` : `已审查 ${hypotheses} 个假设中的 ${reviews} 个。`}还有 ${hypotheses - reviews} 项决定有待完成。`;
  return decided ? `已比较到${decided}。` : `${hypotheses} 个假设 · ${reviews} 项审查`;
}
const holdReasons: [string, string][] = [
  ["429", "模型调用被账户的使用上限拒绝。"], ["AUTH_REQUIRED", "模型账户的身份验证被拒绝。"], ["403", "模型账户的身份验证被拒绝。"],
  ["MODEL_NOT_SUPPORTED", "此 ChatGPT Codex 账户不支持该模型。"], ["400", "模型拒绝了该请求。"],
];
function holdLine(status: ResearchStatus): string {
  const reason = status.current_result?.terminal_reason ?? "";
  return holdReasons.find(([code]) => reason.includes(code))?.[1] ?? "模型调用在撰写回答之前停止了。";
}
export function missingFor(status: ResearchStatus, facts: WorkFacts, labels: string[], total: number | null, hypotheses: number | null, reviews: number | null): string | null {
  const phase = status.attempt?.phase ?? "";
  if (phase === "EVIDENCE_FOCUS") return evidenceFocusLine(facts, labels, total);
  if ((phase === "HYPOTHESIS_REVIEW" || phase === "HYPOTHESES") && hypotheses != null) return hypothesisLine(facts, hypotheses, reviews);
  if (phase === "CONNECTED_SOURCES") return `正在打开${join(facts.sources, 2) || "已连接的资料"}的列表；回答尚未撰写。`;
  if (phase === "SOURCE_SHORTLIST") return `正在从${join(facts.sources, 2) || "已连接的资料"}中选择段落；回答尚未撰写。`;
  return phase === "HOLD" ? holdLine(status) : null;
}
export function stageActionPayload(stage: CompletedStage): string | null {
  const count = Array.isArray(stage.dispatch_ids) ? stage.dispatch_ids.length : null;
  return compactPayload([formatDuration(stage.elapsed_ms), formatBytes(stage.context_bytes), count != null && count > 0 ? `${count} 次模型调用` : null]);
}
export function modelActionPayload(status?: ResearchStatus): { payload: string | null; live: boolean } | null {
  const active = dispatch(status); if (!active) return null;
  const observation = active.transport_observation, received = observation?.received_bytes ?? active.received_bytes;
  const elapsed = formatDuration(observation?.elapsed_ms), first = formatDuration(observation?.first_byte_ms ?? observation?.first_response_ms);
  return active.state === "RESERVED" ? {live: true, payload: compactPayload([elapsed ?? "耗时未知", first ? `首字节 ${first}` : "尚未收到首字节"])}
    : {live: false, payload: compactPayload([typeof received === "number" ? `${received.toLocaleString("zh-CN")}B` : null, elapsed])};
}
export function hypothesisActionPayload(hypotheses: number | null, reviews: number | null): string | null {
  if (hypotheses == null) return null;
  if (reviews == null) return `${hypotheses} 个假设 · 尚无决定`;
  if (reviews < hypotheses) return `${hypotheses} 个假设中已有 ${reviews} 个作出决定`;
  return `${hypotheses} 个假设 · ${reviews} 项审查`;
}