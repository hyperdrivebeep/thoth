import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Button, Callout, TextArea } from "@blueprintjs/core";

import { rpc } from "../api/rpcClient";
import type {
  CriterionProjection,
  MemoryRecord,
  PackRunResult,
  ProtectedActionCard,
  SemanticRevision,
} from "../types";
import { StatusBadge } from "./StatusBadge";
import { ResearchHistoryWorkspace } from "./history/ResearchHistoryWorkspace";

type AuditBundle = {
  revisions: Array<{
    label: string;
    entityType: "EVIDENCE" | "HYPOTHESIS" | "ACTION";
    entityId: string;
    items: SemanticRevision[];
  }>;
  currentProjections: Record<
    string,
    { head_digest: string; revision_id: string }
  >;
  criteria: CriterionProjection[];
  memories: MemoryRecord[];
  excludedMemoryReasons: Record<string, number>;
  receipt: {
    receipt_id: string;
    stored_digest: string;
    semantic_truth_certified: boolean;
  };
  preflight: ProtectedActionCard[];
};

async function loadAuditBundle(result: PackRunResult): Promise<AuditBundle> {
  const projectId = result.project.project_id;
  const objectId = result.thread.current_object_ids[0];
  const entities = [
    ["Evidence assessment", "EVIDENCE", result.cycle.assessment.assessment_id],
    ["Hypothesis portfolio", "HYPOTHESIS", result.cycle.portfolio.portfolio_id],
    ["Action plan", "ACTION", result.cycle.action_plan.plan_id],
  ] as const;
  const revisionResponses = await Promise.all(
    entities.map(([label, entityType, entityId]) =>
      rpc<{ revisions: SemanticRevision[] }>(
        "revision/history/read",
        { project_id: projectId, entity_type: entityType, entity_id: entityId },
        operationKey(`ui-history-${projectId}-${entityType}-${entityId}`),
      ).then((response) => ({ label, entityType, entityId, items: response.value.revisions })),
    ),
  );
  const [criteriaResponse, memory, receiptRead, preflight, headResponse] =
    await Promise.all([
    rpc<{ criteria: Array<CriterionProjection | Record<string, unknown>> }>(
      "criteria/list",
      { project_id: projectId },
      operationKey(`ui-criteria-${projectId}`),
    ),
    rpc<{
      memory_context: {
        included_records: MemoryRecord[];
        excluded_reason_counts: Record<string, number>;
      };
    }>(
      "memory/context/read",
      { project_id: projectId },
      operationKey(`ui-memory-${projectId}`),
    ),
    rpc<{ receipt: { receipt_id: string; receipt_digest: string; semantic_truth_certified: boolean } }>(
      "receipt/read",
      { project_id: projectId, receipt_id: result.cycle.commit.receipt.receipt_id },
      `ui-receipt-read-${result.cycle.commit.receipt.receipt_id}`,
    ),
    rpc<{ cards: ProtectedActionCard[] }>(
      "action/preflight/read",
      { project_id: projectId, object_id: objectId },
      operationKey(`ui-preflight-${projectId}-${objectId}`),
    ),
    rpc<{working_heads:Record<string,string>}>("revision/head/read", {project_id:projectId}, operationKey("ui-current-heads")),
  ]);
  const currentProjections = Object.fromEntries(
    revisionResponses.flatMap((group) => {
      const head = headResponse.value.working_heads[`${group.entityType}:${group.entityId}`];
      const current = group.items.find(item => item.revision_digest === head);
      return current
        ? [[group.entityType, { head_digest: current.revision_digest, revision_id: current.revision_id }]]
        : [];
    }),
  );
  const criteria = criteriaResponse.value.criteria.flatMap((item) => {
    if ("value" in item) return [item as CriterionProjection];
    const full = item as {
      criterion_id?: string;
      identity?: { name?: string };
      usage_authorization?: string;
      required_evidence?: string[];
      revision_digest?: string;
    };
    if (!full.criterion_id || !full.revision_digest) return [];
    return [
      {
        head_digest: full.revision_digest,
        revision_id: full.revision_digest,
        value: {
          criterion_id: full.criterion_id,
          name: full.identity?.name ?? full.criterion_id,
          authority_state: full.usage_authorization ?? "NOT_AUTHORIZED",
          evaluator_input_allowed:
            full.usage_authorization === "AUTHORIZED_EVALUATOR_INPUT",
          reference_candidate: false,
          evidence_refs: full.required_evidence ?? [],
        },
      },
    ];
  });
  const storedDigest = receiptRead.value.receipt.receipt_digest;
  return {
    revisions: revisionResponses,
    currentProjections,
    criteria,
    memories: memory.value.memory_context.included_records,
    excludedMemoryReasons: memory.value.memory_context.excluded_reason_counts,
    receipt: {
      receipt_id: receiptRead.value.receipt.receipt_id,
      stored_digest: storedDigest,
      semantic_truth_certified: receiptRead.value.receipt.semantic_truth_certified,
    },
    preflight: preflight.value.cards,
  };
}

function MemoryPanel({ bundle }: { bundle: AuditBundle }) {
  return (
    <section className="result-section">
      <div className="section-title-row">
        <h2>Project Memory</h2>
        <span>{bundle.memories.length} active records</span>
      </div>
      <div className="card-grid">
        {bundle.memories.map((memory) => (
          <article className="analysis-card" key={memory.memory_id}>
            <div className="card-kicker">
              <StatusBadge value={memory.kind} />
              <StatusBadge value={memory.recall_eligibility} />
            </div>
            <h3>{memory.source_ref ?? memory.owner_revision_ref}</h3>
            <p className="muted">같은 프로젝트의 현재 revision에서만 recall됩니다.</p>
            <code>{memory.revision_digest}</code>
          </article>
        ))}
      </div>
      {Object.keys(bundle.excludedMemoryReasons).length > 0 && (
        <p className="muted">제외 사유: {JSON.stringify(bundle.excludedMemoryReasons)}</p>
      )}
    </section>
  );
}

function TrustPanel({ bundle, result }: { bundle: AuditBundle; result: PackRunResult }) {
  const verify = useMutation({ mutationFn: () => rpc<{verification:{state:string}}>("receipt/verify",
    {project_id:result.project.project_id,receipt_id:bundle.receipt.receipt_id},operationKey("ui-receipt-verify")) });
  return (
    <>
      <section className="result-section">
        <div className="section-title-row">
          <h2>평가기준 계약</h2>
          <span>{bundle.criteria.length} criteria</span>
        </div>
        <div className="card-grid">
          {bundle.criteria.map((criterion) => (
            <article className="analysis-card" key={criterion.value.criterion_id}>
              <div className="card-kicker">
                <StatusBadge value={criterion.value.authority_state} />
                <StatusBadge value={criterion.value.evaluator_input_allowed ? "EVALUATOR_READY" : "HOLD"} />
              </div>
              <h3>{criterion.value.name}</h3>
              <p>{criterion.value.evidence_refs.length} source references</p>
              <code>{criterion.head_digest}</code>
            </article>
          ))}
        </div>
      </section>
      <section className="result-section">
        <div className="section-title-row">
          <h2>보호 행동 preflight</h2>
          <span>{bundle.preflight.length} protected cards</span>
        </div>
        {bundle.preflight.length === 0 ? (
          <p className="empty-copy">현재 action plan에는 R3 보호 행동이 없습니다.</p>
        ) : (
          <div className="card-grid">
            {bundle.preflight.map((card) => (
              <article className="analysis-card protected" key={card.action_id}>
                <StatusBadge value="HUMAN_REQUIRED_R3" />
                <h3>{card.tool}</h3>
                <p>{card.environment} · egress {card.egress}</p>
                <p className="muted">요구 역할: {card.required_roles.join(", ")}</p>
                <code>{card.target_revision}</code>
              </article>
            ))}
          </div>
        )}
      </section>
      <section className="result-section receipt-card">
        <div>
          <p className="eyebrow">CONTENT-BOUND RECEIPT</p>
          <h2>{bundle.receipt.receipt_id}</h2>
          <code>{bundle.receipt.stored_digest}</code>
        </div>
        <div>
          <StatusBadge value={verify.data?.value.verification.state ?? "NOT_VERIFIED_IN_THIS_VIEW"} />
          <StatusBadge value={result.cycle.commit.disposition} />
          <p className="muted">
            저장된 digest를 표시합니다. 검증 버튼은 서버에 새 검증 기록을 만듭니다. 유효한 해시는 과학적 진실의 인증이 아닙니다.
          </p>
          <Button small icon="shield" loading={verify.isPending} onClick={()=>verify.mutate()}>무결성 검증 실행</Button>
          {verify.error && <Callout compact intent="danger">{verify.error.message}</Callout>}
        </div>
      </section>
    </>
  );
}

type ClosureView = {
  closure_id: string;
  status: string;
  resolution: string;
  unresolved_refs: string[];
  open_effect_refs: string[];
  closed_at: string | null;
};

type LocalExport = {
  export_id: string;
  project_id: string;
  purpose: string;
  audience: string;
  head_set_digest: string;
  artifact_refs: string[];
  receipt_refs: string[];
  release_state: string;
};

function downloadJson(filename: string, value: unknown) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function LifecyclePanel({ result }: { result: PackRunResult }) {
  const [resolution, setResolution] = useState(
    "현재 프로젝트의 근거·가설·행동·Outcome 상태를 bounded local cycle로 봉인",
  );
  const [unresolved, setUnresolved] = useState("");
  const [closure, setClosure] = useState<{
    value: ClosureView;
    head: string;
  } | null>(null);
  const [preparedExport, setPreparedExport] = useState<LocalExport | null>(null);
  const projectId = result.project.project_id;
  const closureId = `closure:${result.thread.thread_id}`;
  const prepare = useMutation({
    mutationFn: async () => {
      await rpc(
        "closure/prepare",
        {
          project_id: projectId,
          thread_id: result.thread.thread_id,
          resolution,
          unresolved_refs: unresolved
            .split("\n")
            .map((item) => item.trim())
            .filter(Boolean),
          open_effect_refs: [],
        },
        operationKey("ui-closure-prepare"),
      );
      const current = await rpc<{ closure: ClosureView; head_digest: string }>(
        "closure/read",
        { project_id: projectId, closure_id: closureId },
        operationKey("ui-closure-read"),
      );
      return { value: current.value.closure, head: current.value.head_digest };
    },
    onSuccess: setClosure,
  });
  const finalize = useMutation({
    mutationFn: async () => {
      if (!closure) throw new Error("마감 preview가 없습니다.");
      await rpc(
        "closure/finalize",
        {
          project_id: projectId,
          closure_id: closure.value.closure_id,
          expected_current_head: closure.head,
        },
        operationKey("ui-closure-finalize"),
      );
      const current = await rpc<{ closure: ClosureView; head_digest: string }>(
        "closure/read",
        { project_id: projectId, closure_id: closure.value.closure_id },
        operationKey("ui-closure-read-final"),
      );
      return { value: current.value.closure, head: current.value.head_digest };
    },
    onSuccess: setClosure,
  });
  const prepareExport = useMutation({
    mutationFn: async () => {
      const response = await rpc<{ export: LocalExport }>(
        "export/prepare",
        {
          project_id: projectId,
          purpose: "local field-validation and authorized handoff",
          audience: "authorized project evaluator",
        },
        operationKey("ui-export-prepare"),
      );
      return response.value.export;
    },
    onSuccess: (value) => {
      setPreparedExport(value);
      downloadJson(`${value.export_id.replaceAll(":", "-")}.json`, value);
    },
  });
  const error = prepare.error ?? finalize.error ?? prepareExport.error;
  return (
    <>
      <section className="result-section">
        <div className="section-title-row">
          <h2>프로젝트 마감</h2>
          <StatusBadge value={closure?.value.status ?? "NOT_PREPARED"} />
        </div>
        <Callout compact intent="warning">이 화면은 기존 로컬 closure/prepare·finalize 흐름입니다. 공식 readiness·처분·열린 항목의 owner/trigger/잔여 위험은 전체 기능의 Closure 계약에서 관리합니다. 로컬 CLOSED를 과학적·기관 승인 완료로 보지 않습니다.</Callout>
        <label className="lifecycle-field">
          마감 결론
          <TextArea fill rows={3} value={resolution} onChange={(event) => setResolution(event.target.value)} />
        </label>
        <label className="lifecycle-field">
          미해결 항목 — 한 줄에 하나
          <TextArea
            fill
            placeholder="비어 있으면 READY 후보가 됩니다."
            rows={3}
            value={unresolved}
            onChange={(event) => setUnresolved(event.target.value)}
          />
        </label>
        <div className="lifecycle-actions">
          <Button disabled={prepare.isPending} loading={prepare.isPending} onClick={() => prepare.mutate()}>
            마감 상태 계산·revision 생성
          </Button>
          <Button
            disabled={closure?.value.status !== "READY" || finalize.isPending}
            loading={finalize.isPending}
            onClick={() => finalize.mutate()}
          >
            READY 상태를 로컬 CLOSED로 확정
          </Button>
        </div>
        {closure && (
          <article className="analysis-card lifecycle-result">
            <StatusBadge value={closure.value.status} />
            <h3>{closure.value.resolution}</h3>
            <p>미해결 {closure.value.unresolved_refs.length} · 열린 효과 {closure.value.open_effect_refs.length}</p>
            <code>{closure.head}</code>
          </article>
        )}
      </section>
      <section className="result-section">
        <div className="section-title-row">
          <h2>목적 제한형 로컬 Export</h2>
          <StatusBadge value={preparedExport?.release_state ?? "NOT_PREPARED"} />
        </div>
        <p className="muted">
          현재 head, artifact와 receipt ID를 묶은 JSON manifest만 만듭니다. 외부 공개나 전송은 수행하지 않습니다.
        </p>
        <Button
          className="primary-action"
          disabled={closure?.value.status !== "CLOSED" || prepareExport.isPending}
          loading={prepareExport.isPending}
          onClick={() => prepareExport.mutate()}
        >
          LOCAL_SEALED manifest 만들기
        </Button>
        {preparedExport && (
          <article className="analysis-card lifecycle-result">
            <StatusBadge value={preparedExport.release_state} />
            <h3>{preparedExport.purpose}</h3>
            <p>{preparedExport.artifact_refs.length} artifacts · {preparedExport.receipt_refs.length} receipts</p>
            <code>{preparedExport.head_set_digest}</code>
          </article>
        )}
        {error && <p className="error-banner">{error.message}</p>}
      </section>
    </>
  );
}

function operationKey(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`;
}

export function AuditTrail({
  result,
  view,
}: {
  result: PackRunResult;
  view: "changes" | "memory" | "trust" | "lifecycle";
  onCanonicalChange?: () => void;
}) {
  const [requested, setRequested] = useState(false);
  const audit = useQuery({
    queryKey: ["audit-bundle", result.project.project_id, result.cycle.commit.receipt.receipt_id],
    queryFn: () => loadAuditBundle(result),
    enabled: requested && view !== "changes",
    refetchOnWindowFocus: false,
  });
  if (view === "changes") return <ResearchHistoryWorkspace projectId={result.project.project_id} threadId={result.thread.thread_id} />;
  if (!requested) return <Callout title="정본 감사 기록 조회" className="record-panel">서버의 기록형 조회를 사용하므로 operation·journal이 남을 수 있습니다. 탭 탐색만으로 실행하지 않습니다.
    <p><Button icon="database" intent="primary" onClick={()=>setRequested(true)}>변경·기억·권한 기록 읽기</Button></p></Callout>;
  if (audit.isPending) return <p className="empty-copy panel-loading">canonical ledger를 다시 읽는 중…</p>;
  if (audit.error) return <Callout intent="danger">{audit.error.message}<p><Button onClick={()=>void audit.refetch()}>감사 조회 다시 시도</Button></p></Callout>;
  if (view === "memory") return <MemoryPanel bundle={audit.data} />;
  if (view === "lifecycle") return <LifecyclePanel result={result} />;
  return <TrustPanel bundle={audit.data} result={result} />;
}
