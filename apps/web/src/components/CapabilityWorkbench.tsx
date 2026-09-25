import { Button, Callout, Checkbox, HTMLSelect, InputGroup, Tag, TextArea } from "@blueprintjs/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useDeferredValue, useState } from "react";
import { capabilityCatalog, filterCapabilities, namespaceLabels, namespaces, parseInvocation, transportFor, type Capability } from "../api/capabilities";
import { rpc, RpcError } from "../api/rpcClient";
import { RecordInspector } from "./RecordInspector";

function MethodDetail({ method, projectId, threadId }: { method: Capability; projectId: string; threadId?: string }) {
  const client = useQueryClient();
  const [input, setInput] = useState(() => JSON.stringify({ project_id: projectId,
    ...(threadId && method.namespace === "thread" ? { thread_id: threadId } : {}),
  }, null, 2));
  const [confirmed, setConfirmed] = useState(false);
  const [requestKey, setRequestKey] = useState(() => crypto.randomUUID());
  let validation = "";
  try { parseInvocation(input, projectId); } catch (error) { validation = error instanceof Error ? error.message : "잘못된 입력"; }
  const directRead = transportFor(method.name) === "/rpc/query";
  const command = method.surface !== "QUERY" && !directRead;
  const invocation = useMutation({
    mutationFn: (captured: { input: Record<string, unknown>; key: string }) => rpc<Record<string, unknown>>(method.name, captured.input, captured.key),
    onSuccess: () => { if (command) void client.invalidateQueries(); },
  });
  return <section className="method-detail" aria-label="기능 상세">
    <div className="section-title-row"><Tag intent={command ? "warning" : "primary"}>{method.surface}</Tag><Tag minimal>{method.canonical ? "canonical" : "compatibility alias"}</Tag></div>
    <h2>{method.name}</h2>
    <dl className="method-meta"><div><dt>정본 owner</dt><dd>{method.canonical_owner}</dd></div><div><dt>정책</dt><dd>{method.policy}</dd></div>
      <div><dt>정상 진입점</dt><dd>{method.normal_entrypoint}</dd></div><div><dt>계약 근거</dt><dd>{method.behavioral_evidence.join(" · ")}</dd></div>
      {method.alias_target && <div><dt>별칭 대상</dt><dd>{method.alias_target}</dd></div>}
      <div><dt>전송 경로</dt><dd>{transportFor(method.name)}</dd></div></dl>
    <Callout compact intent={command ? "warning" : "primary"} title={command ? "고급 명령 · 명시 실행" : directRead ? "비변경 조회" : "기록형 조회"}>
      {command ? "정본 변경·모델/외부 호출·비용이 발생할 수 있습니다. 아래 정확한 입력을 확인하세요. 서버의 권한·예산·현재성 검사는 그대로 적용됩니다." :
        directRead ? "서버의 읽기 전용 허용 목록을 사용합니다. 조회만으로 operation이나 영수증을 만들지 않습니다." :
        "QUERY 계약이지만 서버의 비변경 허용 목록에는 없습니다. 명시 실행 시 /rpc가 operation·journal을 기록할 수 있습니다. 탐색만으로 실행하지 않습니다."}
    </Callout>
    <label className="editor-label" htmlFor="rpc-input">입력 JSON <small>project_id만 주입됩니다. 필요한 ID·digest·DTO는 계약에 맞게 직접 입력하세요.</small></label>
    <TextArea id="rpc-input" className="json-editor" fill rows={9} spellCheck={false} value={input} onChange={event => {
      setInput(event.target.value); setConfirmed(false); setRequestKey(crypto.randomUUID()); invocation.reset();
    }} />
    <p className="muted">이 화면은 기술 RPC 진입입니다. 카탈로그 존재나 IMPLEMENTED 표기는 모든 경로의 UI·live 검증을 의미하지 않습니다.</p>
    {validation && <Callout compact intent="warning">{validation}</Callout>}
    {command && <Checkbox checked={confirmed} label="이 메서드와 입력의 변경·외부 효과 가능성을 확인하고 실행합니다." onChange={event => setConfirmed(event.currentTarget.checked)} />}
    <div className="method-actions"><Button icon={command ? "play" : "search"} intent={command ? "warning" : "primary"} loading={invocation.isPending}
      disabled={Boolean(validation) || (command && !confirmed)} onClick={() => invocation.mutate({ input: parseInvocation(input, projectId), key: requestKey })}>
      {command ? "확인한 명령 실행" : "이 조회 실행"}</Button>
      <Button minimal disabled={invocation.isPending} onClick={() => { setRequestKey(crypto.randomUUID()); setConfirmed(false); invocation.reset(); }}>새 요청 키</Button></div>
    <small className="request-key">재시도 key: {requestKey} · 같은 입력의 재시도는 같은 key를 사용합니다.</small>
    {invocation.error && <Callout intent="danger" title="실행 거부 또는 실패"><p>{invocation.error.message}</p>{invocation.error instanceof RpcError && <RecordInspector value={invocation.error.details} />}</Callout>}
    {invocation.data && <div className="rpc-result"><div className="section-title-row"><h3>서버 응답</h3><Tag>{invocation.data.state}</Tag></div>
      {invocation.data.operation_id && <p className="muted">operation: {invocation.data.operation_id} · 접수/실행 상태는 도메인 완료와 다릅니다.</p>}
      <RecordInspector value={invocation.data.value} /></div>}
  </section>;
}

export function CapabilityWorkbench({ projectId, threadId, initialNamespace = "all" }: { projectId: string; threadId?: string; initialNamespace?: string }) {
  const [search, setSearch] = useState("");
  const [namespace, setNamespace] = useState(initialNamespace);
  const [surface, setSurface] = useState("all");
  const [selected, setSelected] = useState("");
  const deferredSearch = useDeferredValue(search);
  const visibleNamespaces = namespaces.filter(value => value !== "projectpack" && value !== "field");
  const methods = filterCapabilities(deferredSearch, namespace, surface).filter(
    method => method.namespace !== "projectpack" && method.namespace !== "field",
  );
  const method = methods.find(item => item.name === selected) ?? methods[0];
  return <section className="capability-workbench">
    <header className="workspace-heading"><div><p className="eyebrow">CAPABILITY WORKBENCH</p><h1>기능 탐색 · 고급 제어</h1><p>모든 공개 계약을 한곳에서. 전용 연구 흐름과 기술 RPC를 구분합니다.</p></div>
      <Tag minimal>{capabilityCatalog.canonical_method_count} canonical + {capabilityCatalog.compatibility_alias_count} aliases</Tag></header>
    <div className="catalog-toolbar"><InputGroup leftIcon="search" aria-label="기능 검색" placeholder="메서드, owner, 정책 검색…" value={search} onChange={event => setSearch(event.target.value)} />
      <HTMLSelect aria-label="기능 namespace" value={namespace} onChange={event => setNamespace(event.target.value)} options={[{value:"all", label:`전체 ${visibleNamespaces.length} namespaces`}, ...visibleNamespaces.map(value => ({value, label:`${value} · ${namespaceLabels[value] ?? value}`}))]} />
      <HTMLSelect aria-label="기능 종류" value={surface} onChange={event => setSurface(event.target.value)} options={[{value:"all",label:"전체 종류"},{value:"QUERY",label:"조회 QUERY"},{value:"COMMAND",label:"명령 COMMAND"},{value:"QUERY_OR_CONTROL",label:"호환 조회·제어"}]} /></div>
    <div className="catalog-layout"><div className="method-list" aria-label="공개 기능 목록"><div className="catalog-count">{methods.length} visible methods</div>
      {methods.length === 0 && <p className="empty-copy">검색에 맞는 기능이 없습니다.</p>}
      {methods.map(item => <Button alignText="left" minimal active={method?.name === item.name} key={item.name} onClick={() => setSelected(item.name)} className="method-item">
        <span className={`method-kind ${item.surface.toLowerCase()}`}>{item.surface === "QUERY" ? "Q" : "C"}</span><span><strong>{item.name}</strong><small>{namespaceLabels[item.namespace] ?? item.namespace}{!item.canonical ? " · alias" : ""}</small></span>
      </Button>)}</div>{method && <MethodDetail key={`${projectId}:${threadId ?? ""}:${method.name}`} method={method} projectId={projectId} threadId={threadId} />}</div>
  </section>;
}
