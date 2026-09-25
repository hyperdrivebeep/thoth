import { Button, Callout, Tag } from "@blueprintjs/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { HistorySelection } from "../../api/historyModels";
import { readDecisionDelta, type DecisionDelta, type ResultIdentity } from "../../api/researchFollowup";
import { currentnessLabel } from "./historyPresentation";

const exactDigest = /^[a-f0-9]{64}$/i;

function identity(selection: HistorySelection): ResultIdentity | null {
  if (selection.kind !== "result" || !exactDigest.test(selection.scope.requestDigest) ||
      !selection.resultDigest || !exactDigest.test(selection.resultDigest)) return null;
  return { request_revision_digest: selection.scope.requestDigest, result_revision_digest: selection.resultDigest };
}

function groupLabel(kind: DecisionDelta["groups"][number]["kind"]): string {
  return { CONTENT: "답변 내용", EVIDENCE: "근거", CONDITION: "적용 조건", STATUS: "상태", ACTION: "다음 행동", OTHER: "그 밖의 변경" }[kind];
}

function changeLabel(path: string): string {
  const labels: Record<string, string> = {
    "/result/answer": "답변", "/phase": "연구 단계", "/completion": "기록 완료 상태",
    "/terminal_reason": "종료 이유", "/gaps": "남은 근거 공백", "/next_steps": "다음 단계",
  };
  return labels[path] ?? path.split("/").filter(Boolean).at(-1)?.replaceAll("_", " ") ?? "변경 항목";
}

const previewLimit = 5;
const valuePreviewLimit = 240;
const detailPageSize = 20;
const technicalPath = /(?:^|\/)(?:[^/]*(?:digest|sha256|checksum|hash|_id|_ref)|id|ref|schema_version|record_refs|metadata|trace_paths)(?:\/|$)/i;
const technicalValue = /[a-f0-9]{64}|[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}/i;

function shortText(value: string): string {
  return value.length > valuePreviewLimit ? `${value.slice(0, valuePreviewLimit)}…` : value;
}

function previewValue(value: unknown, depth = 0): string {
  if (value === null || value === undefined) return "값 없음";
  if (typeof value === "string") return technicalValue.test(value) ? "기술 식별자 포함 · 전체 값에서 확인" : shortText(value);
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return depth >= 2 ? `목록 ${value.length}개`
    : shortText(`목록 ${value.length}개${value.length ? ` · 첫 항목: ${previewValue(value[0], depth + 1)}` : ""}`);
  if (typeof value === "object") {
    const entries = Object.entries(value);
    if (depth >= 2) return `필드 ${entries.length}개`;
    const readable = entries.filter(([name]) => !technicalPath.test(`/${name}`));
    const representative = ["answer", "summary", "statement", "status", "reason"].map(key => readable.find(([name]) => name === key)).find(Boolean) ?? readable[0];
    return representative ? shortText(`필드 ${entries.length}개 · ${representative[0]}: ${previewValue(representative[1], depth + 1)}`)
      : `필드 ${entries.length}개 · 세부 값 펼치기`;
  }
  return "표시할 수 없는 값";
}

function DecisionValue({ value }: { value: unknown }) {
  const [open, setOpen] = useState(false);
  const expandable = (typeof value === "string" && (value.length > valuePreviewLimit || technicalValue.test(value))) || (typeof value === "object" && value !== null);
  return <div className="decision-value">
    <span>{previewValue(value)}</span>
    {expandable && <Button small minimal aria-expanded={open} onClick={() => setOpen(current => !current)}>{open ? "전체 값 닫기" : "전체 값 보기"}</Button>}
    {open && <pre className="decision-value-full">{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</pre>}
  </div>;
}

function previewSelection(groups: DecisionDelta["groups"]): Set<string> {
  const selected = new Set<string>();
  const candidates = groups.map((group, groupIndex) => group.kind === "OTHER" ? []
    : group.changes.map((change, index) => technicalPath.test(change.path) ? null : `${groupIndex}:${index}`).filter((value): value is string => value !== null));
  for (let round = 0; selected.size < previewLimit; round += 1) {
    let added = false;
    for (const entries of candidates) {
      if (entries[round]) {
        selected.add(entries[round]);
        added = true;
        if (selected.size === previewLimit) break;
      }
    }
    if (!added) break;
  }
  return selected;
}

function DecisionGroup({ group, groupIndex, preview }: { group: DecisionDelta["groups"][number]; groupIndex: number; preview: Set<string> }) {
  const [open, setOpen] = useState(false);
  const [limit, setLimit] = useState(detailPageSize);
  const previewIndexes = group.changes.map((_, index) => index).filter(index => preview.has(`${groupIndex}:${index}`));
  const shownIndexes = open ? [...new Set([...previewIndexes, ...group.changes.slice(0, limit).map((_, index) => index)])].sort((a, b) => a - b) : previewIndexes;
  const remaining = group.changes.length - shownIndexes.length;
  return <section className="decision-delta-group">
    <div className="section-title-row"><h4>{groupLabel(group.kind)} <span className="muted">{group.changes.length}건</span></h4>
      {group.changes.length > previewIndexes.length && <Button small minimal aria-expanded={open} onClick={() => setOpen(current => !current)}>
        {open ? "상세 닫기" : `상세 ${group.changes.length - previewIndexes.length}건 보기`}
      </Button>}</div>
    {shownIndexes.map(index => {
      const change = group.changes[index];
      return <article className="decision-delta-change" key={`${change.path}-${index}`}>
        <h5>{changeLabel(change.path)}</h5>
        <div className="decision-delta-values">
          <div><strong>비교 전</strong>{change.before_present ? <DecisionValue value={change.before} /> : <span>기록 없음</span>}</div>
          <div><strong>비교 후</strong>{change.after_present ? <DecisionValue value={change.after} /> : <span>기록 없음</span>}</div>
        </div>
      </article>;
    })}
    {remaining > 0 && <p className="muted">이 그룹에서 아직 펼치지 않은 변경 {remaining}건</p>}
    {open && limit < group.changes.length && <Button small onClick={() => setLimit(current => current + detailPageSize)}>다음 변경 20건 보기</Button>}
  </section>;
}

export function DecisionDeltaView({ delta }: { delta: DecisionDelta }) {
  const [reasonDetailsOpen, setReasonDetailsOpen] = useState(false);
  const totalChanges = delta.groups.reduce((total, group) => total + group.changes.length, 0);
  const preview = previewSelection(delta.groups);
  return <section className="decision-delta-view" aria-label="선택한 두 답변의 판단 변화">
    <div className="section-title-row"><h3>답변 전체 변경</h3><Tag minimal intent={delta.state === "UNAVAILABLE" ? "warning" : "none"}>
      {delta.state === "CHANGED" ? "변경 있음" : delta.state === "NO_CHANGE" ? "변경 없음" : delta.state === "PARTIAL" ? "일부만 확인" : "확인 불가"}
    </Tag></div>
    {delta.state === "NO_CHANGE" && <p>서버가 기록한 두 답변의 판단 변화가 없습니다.</p>}
    {delta.state === "PARTIAL" && <Callout compact intent="warning">일부 변경만 확인됐습니다. 빠진 항목을 변화 없음으로 해석하지 마세요.</Callout>}
    {delta.state === "UNAVAILABLE" && <Callout compact intent="warning">현재 권한 또는 기록 상태에서 비교 내용을 읽을 수 없습니다.</Callout>}
    {delta.state !== "UNAVAILABLE" && totalChanges > 0 && <p className="decision-delta-count">서버 기록 변경 {totalChanges}건 중 {preview.size}건을 먼저 표시합니다. 나머지 {totalChanges - preview.size}건은 그룹별 상세에서 확인할 수 있습니다.</p>}
    {delta.state !== "UNAVAILABLE" && delta.groups.map((group, index) => <DecisionGroup key={`${group.kind}-${index}`} group={group} groupIndex={index} preview={preview} />)}
    {delta.state === "CHANGED" && delta.groups.length === 0 && <p>변경 상태가 기록됐지만 표시할 세부 항목은 없습니다.</p>}
    <div className="decision-delta-reasons"><h4>기록된 변경 이유</h4>
      {delta.reason_state === "UNKNOWN_REASON" ? <p>변경 이유가 기록되지 않았습니다. 이유를 추정하지 않습니다.</p>
        : <>{delta.reason_codes.length ? <ul>{delta.reason_codes.slice(0, 3).map((code, index) => <li key={`${code}-${index}`}>{code}</li>)}</ul> : <p>기록된 이유 문구가 없습니다.</p>}
          {(delta.reason_codes.length > 3 || delta.reason_refs.length > 0) && <Button small minimal aria-expanded={reasonDetailsOpen} onClick={() => setReasonDetailsOpen(value => !value)}>
            {reasonDetailsOpen ? "이유 상세 닫기" : `이유 상세 보기${delta.reason_refs.length ? ` · 연결 기록 ${delta.reason_refs.length}건` : ""}`}
          </Button>}
          {reasonDetailsOpen && delta.reason_codes.length > 3 && <ul>{delta.reason_codes.slice(3).map((code, index) => <li key={`${code}-${index + 3}`}>{code}</li>)}</ul>}
          {reasonDetailsOpen && delta.reason_refs.length > 0 && <p>연결 기록: {delta.reason_refs.map((ref, index) => {
            const kind = typeof ref.entity_type === "string" ? ref.entity_type : "기록";
            const id = typeof ref.entity_id === "string" ? ref.entity_id : "식별자 없음";
            return <span key={index}>{index > 0 ? " · " : ""}{kind} {id}</span>;
          })}</p>}</>}
    </div>
    <p className="muted">비교 전: {currentnessLabel(delta.basis_currentness.before ?? { state: "UNKNOWN_BASIS", reasons: [] })} · 비교 후: {currentnessLabel(delta.basis_currentness.after ?? { state: "UNKNOWN_BASIS", reasons: [] })}</p>
  </section>;
}

export function DecisionDeltaComparison({ before, after, actorScope, historyCheckEpoch = 0, suspended = false }: {
  before: HistorySelection; after: HistorySelection; actorScope: string | null; historyCheckEpoch?: number; suspended?: boolean;
}) {
  const beforeId = identity(before);
  const afterId = identity(after);
  const sameScope = before.scope.projectId === after.scope.projectId && before.scope.threadId === after.scope.threadId;
  const distinct = Boolean(beforeId && afterId && (beforeId.request_revision_digest !== afterId.request_revision_digest || beforeId.result_revision_digest !== afterId.result_revision_digest));
  const ready = Boolean(actorScope && beforeId && afterId && sameScope && distinct);
  const comparison = useQuery({
    queryKey: ["decision-delta", actorScope, before.scope.projectId, before.scope.threadId,
      beforeId?.request_revision_digest, beforeId?.result_revision_digest, afterId?.request_revision_digest, afterId?.result_revision_digest, historyCheckEpoch],
    enabled: ready, retry: false, gcTime: 1000,
    // Keep this pure read alive across StrictMode's transient unsubscribe. The exact
    // actor/identity key fences late data, while rpc() still enforces its 15s timeout.
    queryFn: () => readDecisionDelta(before.scope.projectId, before.scope.threadId!, beforeId!, afterId!),
  });
  if (!beforeId || !afterId) return <p role="status">요청·결과의 정확한 연결이 없는 기록은 비교할 수 없습니다.</p>;
  if (!sameScope) return <p role="status">같은 프로젝트와 세션의 답변 두 개를 선택해 주세요.</p>;
  if (!distinct) return <p role="status">서로 다른 답변 두 개를 선택해 주세요.</p>;
  if (!actorScope) return <p role="status">접근 범위를 확인한 뒤 비교할 수 있습니다.</p>;
  if (suspended) return <p role="status">접근 범위를 다시 확인하는 중… 선택한 두 답변은 유지합니다.</p>;
  if (comparison.isPending || comparison.isFetching) return <p role="status">선택한 두 답변을 비교하는 중…</p>;
  if (comparison.error) return <Callout intent="warning" role="alert">두 답변의 비교를 읽지 못했습니다. 현재 권한과 기록 연결을 확인해 주세요.
    <Button small onClick={() => void comparison.refetch()}>다시 비교</Button></Callout>;
  return <DecisionDeltaView delta={comparison.data} />;
}
