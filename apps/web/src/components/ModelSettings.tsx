import { Button, HTMLSelect } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { rpc } from "../api/rpcClient";

export type ModelSelection = { provider: string; model: string; reasoning_effort?: string };
type ModelOption = { provider: string; model: string; reasoning_efforts: string[]; default_effort: string | null };
type SavedSelection = { provider: string | null; model: string | null; reasoning_effort?: string | null };
type EffectiveSettings = { provider: string; model: string | null; reasoning_effort?: string | null };
type Settings = { settings_digest: string | null; selection?: SavedSelection; effective_settings: EffectiveSettings | null;
  availability?: "AVAILABLE" | "UNAVAILABLE"; reason_code?: string | null; model_options: ModelOption[] };

function unavailableReason(code?: string | null): string {
  if (code === "MODEL_CAPABILITY_UNKNOWN") return "현재 모델 목록에서 저장된 모델을 찾지 못했습니다.";
  if (code === "MODEL_REASONING_EFFORT_UNSUPPORTED") return "저장된 추론강도를 현재 모델에서 사용할 수 없습니다.";
  if (code === "MODEL_ROUTE_INCOMPLETE") return "저장된 모델 경로가 완전하지 않습니다.";
  return code ? `사용 불가 이유: ${code}` : "사용 불가 이유를 확인하지 못했습니다.";
}

function savedLabel(value?: SavedSelection): string | null {
  if (!value?.provider && !value?.model && !value?.reasoning_effort) return null;
  return `${value.provider ?? "공급자 미지정"} / ${value.model ?? "모델 미지정"}${value.reasoning_effort ? ` · 추론강도 ${value.reasoning_effort}` : ""}`;
}

export function ModelSettings({ projectId, threadId, selection, onSelect, onSaved, compact = false }: {
  projectId: string; threadId?: string; selection: ModelSelection | null;
  onSelect: (selection: ModelSelection) => void; onSaved: () => void; compact?: boolean;
}) {
  const client = useQueryClient();
  const [explicitReplacement, setExplicitReplacement] = useState<{ scope: string; readAt: number; choice: ModelSelection } | null>(null);
  const scope = { project_id: projectId, ...(threadId ? { thread_id: threadId } : {}) };
  const scopeIdentity = JSON.stringify([projectId, threadId ?? null]);
  const key = ["model-settings", projectId, threadId];
  const query = useQuery({ queryKey: key, queryFn: ({ signal }) =>
    rpc<Settings>("model/settings/read", scope, crypto.randomUUID(), signal) });
  const data = query.isFetching || query.error ? undefined : query.data?.value;
  const effective = data?.effective_settings;
  const saved = data?.selection;
  const inheritsProject = Boolean(threadId && saved && !saved.provider && !saved.model && !saved.reasoning_effort);
  const inherited = useQuery({ queryKey: ["model-settings", projectId, threadId, "project-parent", query.dataUpdatedAt], enabled: inheritsProject && data?.availability === "UNAVAILABLE",
    queryFn: ({ signal }) => rpc<Settings>("model/settings/read", { project_id: projectId }, crypto.randomUUID(), signal), retry: false,
    gcTime: 0, refetchOnMount: "always" });
  const inheritedLabel = savedLabel(inherited.data?.value.selection);
  const savedChoice = saved?.provider && saved.model ? { provider: saved.provider, model: saved.model,
    ...(saved.reasoning_effort ? { reasoning_effort: saved.reasoning_effort } : {}) } : undefined;
  const effectiveChoice = effective?.model ? { provider: effective.provider, model: effective.model,
    ...(effective.reasoning_effort ? { reasoning_effort: effective.reasoning_effort } : {}) } : undefined;
  const chosenReplacement = explicitReplacement?.scope === scopeIdentity && explicitReplacement.readAt === query.dataUpdatedAt
    ? explicitReplacement.choice : null;
  const current = data?.availability === "UNAVAILABLE" ? chosenReplacement ?? savedChoice : selection ?? effectiveChoice ?? savedChoice;
  const option = data?.model_options.find(o => o.provider === current?.provider && o.model === current?.model);
  const validEffort = data?.availability === "UNAVAILABLE" && chosenReplacement && option?.reasoning_efforts.length
    ? Boolean(current?.reasoning_effort && option.reasoning_efforts.includes(current.reasoning_effort))
    : !current?.reasoning_effort || Boolean(option?.reasoning_efforts.includes(current.reasoning_effort));
  const explicitlyChosen = data?.availability !== "UNAVAILABLE" || Boolean(chosenReplacement);
  const choose = (next: ModelSelection) => {
    const chosen = { provider: next.provider, model: next.model, reasoning_effort: next.reasoning_effort };
    if (data?.availability === "UNAVAILABLE") setExplicitReplacement({ scope: scopeIdentity, readAt: query.dataUpdatedAt, choice: chosen });
    onSelect(chosen);
  };
  const save = useMutation({ mutationFn: async (captured: {
    scope: typeof scope; selection: ModelSelection; digest: string | null;
    key: typeof key; onSaved: () => void;
  }) => {
    const response = await rpc<Settings>("model/settings/update", {
      ...captured.scope, selection: captured.selection, expected_digest: captured.digest,
    }, crypto.randomUUID());
    return {response, captured};
  }, onSuccess: async ({response, captured}) => {
    client.setQueryData(captured.key, response);
    await client.invalidateQueries({ queryKey: captured.scope.thread_id
      ? captured.key : ["model-settings", captured.scope.project_id] });
    captured.onSaved();
  } });
  return <fieldset className={`model-settings ${compact ? "compact" : ""}`}>
    <legend>모델 설정</legend>
    {data?.availability === "UNAVAILABLE" && <p role="status" className="result-notice">저장된 모델 선택을 현재 사용할 수 없습니다. {unavailableReason(data.reason_code)}
      {savedLabel(saved) ? ` 저장된 선택: ${savedLabel(saved)}.` : ""}
      {inheritsProject ? " 이 작업은 프로젝트 모델 설정을 상속하며 별도 선택으로 대체하지 않았습니다." : ""}
      {" "}기존 선택은 유지됩니다. 변경하려면 아래에서 모델을 직접 고르세요.</p>}
    {inheritsProject && data?.availability === "UNAVAILABLE" && (inherited.isPending || inherited.isFetching ? <small role="status">상속한 프로젝트 모델을 확인하는 중…</small>
      : inherited.error ? <small role="alert">상속한 프로젝트 모델을 읽지 못했습니다.</small>
        : <small>상속한 프로젝트 선택: {inheritedLabel ?? "명시된 프로젝트 모델 없음"}.</small>)}
    {inheritsProject && data?.availability !== "UNAVAILABLE" && <small>이 작업은 프로젝트 모델 설정을 상속합니다.</small>}
    <label>모델 <HTMLSelect key={`${scopeIdentity}:${query.dataUpdatedAt}`} aria-label="연구 모델" disabled={!data} value={option ? `${option.provider}/${option.model}` : ""}
      onChange={event => {
        const next = data?.model_options.find(o => `${o.provider}/${o.model}` === event.target.value);
        if (next) choose({ provider: next.provider, model: next.model,
          ...(data?.availability !== "UNAVAILABLE" && next.default_effort ? { reasoning_effort: next.default_effort } : {}) });
      }}><option value="">모델 선택</option>{data?.model_options.map(o =>
        <option key={`${o.provider}/${o.model}`} value={`${o.provider}/${o.model}`}>{o.model} · {o.provider}</option>)}</HTMLSelect></label>
    <label>추론강도 <HTMLSelect aria-label="연구 추론강도" disabled={!option} value={current?.reasoning_effort ?? ""}
      onChange={event => { if (current) choose({ ...current, reasoning_effort: event.target.value }); }}>
      <option value="">기본값</option>{option?.reasoning_efforts.map(e => <option key={e} value={e}>{e}</option>)}</HTMLSelect></label>
    <Button className="model-defaults" small minimal icon="floppy-disk" disabled={!current || !option || !validEffort || !explicitlyChosen || !data || save.isPending} loading={save.isPending} onClick={() => {
      if (current && option && validEffort && explicitlyChosen && data) save.mutate({scope, selection: current, digest: data.settings_digest, key, onSaved});
    }}>
      {threadId ? "이 작업의 기본값으로 저장" : "프로젝트 기본값으로 저장"}</Button>
    <small>저장하지 않은 선택은 다음 요청 한 번에 적용됩니다. 모델 목록의 가용성은 공급자 인증이나 실행 성공을 뜻하지 않습니다.</small>
    {save.isSuccess && <small role="status">기본값을 저장했습니다.</small>}
    {(query.error || save.error) && <span role="alert">{(query.error ?? save.error)?.message}</span>}
  </fieldset>;
}
