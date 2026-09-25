import { useMemo, useState } from "react";
import { Callout, HTMLSelect, InputGroup } from "@blueprintjs/core";

import type { EvidenceSpan } from "../types";
import { StatusBadge } from "./StatusBadge";

function locator(span: EvidenceSpan) {
  const values = [
    span.locator.page ? `p.${span.locator.page}` : null,
    span.locator.line ? `line ${span.locator.line}` : null,
    span.locator.sheet ?? null,
    span.locator.cell_range ?? null,
    span.locator.xml_path ?? null,
    span.locator.uri ?? null,
  ];
  return values.filter(Boolean).join(" · ") || "구조 위치 없음";
}

export function EvidenceRail({
  evidence,
  totalCount = evidence.length,
  loading = false,
  error,
  heading,
}: {
  evidence: EvidenceSpan[];
  totalCount?: number;
  loading?: boolean;
  error?: string;
  heading?: string;
}) {
  const [query, setQuery] = useState("");
  const [authority, setAuthority] = useState("ALL");
  const filtered = useMemo(
    () =>
      evidence.filter(
        (span) =>
          (authority === "ALL" || span.authority_state === authority) &&
          (!query.trim() ||
            span.exact_text.toLowerCase().includes(query.trim().toLowerCase()) ||
            span.span_id.toLowerCase().includes(query.trim().toLowerCase())),
      ),
    [authority, evidence, query],
  );
  return (
    <aside className="evidence-rail" aria-label="근거 레일">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">EVIDENCE</p>
          <h2>{loading ? "근거 조회 중" : error ? "근거 조회 실패" : heading ?? `활성 근거 ${evidence.length}개`}</h2>
          {totalCount > evidence.length && <span>전체 {totalCount.toLocaleString("ko-KR")}개 중 선택</span>}
        </div>
      </div>
      {evidence.length > 0 && (
        <div className="evidence-filters">
          <InputGroup
            leftIcon="search"
            aria-label="근거 검색"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="문장·span ID 검색"
            value={query}
          />
          <HTMLSelect
            aria-label="근거 권위 필터"
            onChange={(event) => setAuthority(event.target.value)}
            value={authority}
          >
            <option value="ALL">모든 권위</option>
            <option value="APPROVED">승인본</option>
            <option value="OFFICIAL">공식</option>
            <option value="INFORMAL">비공식 참고</option>
            <option value="UNCLASSIFIED">미분류</option>
          </HTMLSelect>
        </div>
      )}
      <div className="evidence-list">
        {loading ? <p className="empty-copy">근거를 읽는 중…</p> : error ? <Callout compact intent="danger">{error}</Callout> : evidence.length === 0 ? (
          <p className="empty-copy">아직 표시할 근거가 없습니다. 자료를 연결하거나 조사를 진행하면 원문과 위치가 표시됩니다.</p>
        ) : (
          filtered.map((span) => (
            <article className="evidence-item" key={span.span_id}>
              <p>{span.exact_text}</p>
              <code>{locator(span)}</code>
              <small>{span.artifact_id} · {span.span_id}</small>
              <div className="badge-row">
                <StatusBadge value={span.authority_state} />
                <StatusBadge value={span.cutoff_state} />
                <StatusBadge value={span.support_state} />
              </div>
            </article>
          ))
        )}
        {evidence.length > 0 && filtered.length === 0 && (
          <p className="empty-copy">조건에 맞는 활성 근거가 없습니다.</p>
        )}
      </div>
    </aside>
  );
}
