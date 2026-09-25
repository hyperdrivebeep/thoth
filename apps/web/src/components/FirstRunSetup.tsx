import { Button, Callout, InputGroup, Tag } from "@blueprintjs/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { rpc } from "../api/rpcClient";
import { credentialAccountLabel, credentialAvailabilityNote, credentialCompanies, credentialConnectionHint, credentialGuideUrl, credentialLocallyConfigured, credentialResultMessage, loginSupported,
  type CredentialAccount, type CredentialRegisterResult } from "./modelCredentialPresentation";

type Ready = {
  ready: boolean;
  model_connected?: boolean;
  deployment_mode?: string;
  disclosure?: string;
  hosted_model?: { provider: string; model: string };
  setup?: { internet_consent: string };
};

export function FirstRunSetup({ onDone }: { onDone: () => void }) {
  const client = useQueryClient();
  const ready = useQuery({
    queryKey: ["workspace-ready"],
    queryFn: () => rpc<Ready>("workspace/ready", {}, "ws-ready"),
  });
  const listed = useQuery({
    queryKey: ["model-credentials", "system:workspace"],
    enabled: ready.isSuccess && ready.data?.value.deployment_mode !== "HOSTED_REVIEW",
    queryFn: () =>
      rpc<{ accounts: CredentialAccount[] }>(
        "model/credential/list",
        { project_id: "system:workspace" },
        "ui-cred-list",
      ),
  });
  const connect = useMutation({
    mutationFn: (input: Record<string, string>) =>
      rpc<CredentialRegisterResult>(
        "model/credential/register",
        { project_id: "system:workspace", ...input },
        crypto.randomUUID(),
      ),
    onSuccess: async (result) => {
      if (result.value?.credential) { setApiKey(""); setKeyProvider(null); }
      await Promise.all([
        client.invalidateQueries({ queryKey: ["model-credentials", "system:workspace"] }),
        client.invalidateQueries({ queryKey: ["model-settings"] }),
        client.invalidateQueries({ queryKey: ["workspace-ready"] }),
      ]);
    },
  });
  const update = useMutation({
    mutationFn: (internet_consent: string) =>
      rpc("workspace/setup/update", { internet_consent }, crypto.randomUUID()),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["workspace-ready"] });
      if (hosted || modelConnected) onDone();
    },
  });
  const value = ready.error || ready.isFetching ? undefined : ready.data?.value;
  const hosted = value?.deployment_mode === "HOSTED_REVIEW";
  const consent = value?.setup?.internet_consent ?? "UNDECIDED";
  const accounts = listed.error || listed.isFetching ? [] : listed.data?.value.accounts ?? [];
  const modelConnected = Boolean(value?.model_connected) && listed.isSuccess && accounts.some(credentialLocallyConfigured);
  const guideUrl = credentialGuideUrl(connect.data?.value);
  const setupReady = hosted ? consent !== "UNDECIDED" : modelConnected && consent !== "UNDECIDED";
  const [step, setStep] = useState<"model" | "internet">("model");
  const [keyProvider, setKeyProvider] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState("");
  const currentStep = hosted ? "internet" : step;

  return (
    <main className="first-run-shell bp6-dark">
      <section className="first-run-card" aria-labelledby="first-run-title">
        <p className="eyebrow">
          {hosted ? "심사 워크스페이스" : `처음 설정 · ${currentStep === "model" ? "1 / 2" : "2 / 2"}`}
        </p>
        {!hosted && currentStep === "model" ? (
          <>
            <h1 id="first-run-title">연구를 맡길 모델을 연결하세요</h1>
            <p className="muted">하나만 있으면 됩니다. 나머지는 나중에 설정에서 추가할 수 있습니다.</p>
            <div className="first-run-providers">
              {credentialCompanies.map((company) => {
                const row = accounts.find((item) => item.provider === company.provider);
                const connected = credentialLocallyConfigured(row);
                const canStartLogin = loginSupported(row);
                const canGuideKey = row?.login_supported === false && row.login_kind === "unsupported";
                const availabilityNote = credentialAvailabilityNote(row);
                const keyOpen = keyProvider === company.provider;
                return (
                  <article className={`first-run-provider${connected ? " connected" : ""}`} key={company.provider}>
                    <div>
                      <strong>{company.label}</strong>
                      <Tag minimal intent={connected ? "success" : "none"}>{credentialAccountLabel(row)}</Tag>
                      <p>{credentialConnectionHint(row)}</p>
                      {availabilityNote && <small>{availabilityNote}</small>}
                    </div>
                    <div className="first-run-provider-actions">
                      <Button disabled={!canStartLogin && !canGuideKey} loading={connect.isPending} onClick={() => connect.mutate({ provider: company.provider })}>
                        {canStartLogin ? "Codex 로그인 시작" : canGuideKey ? "키 발급 안내" : "연결 방법 확인 중"}
                      </Button>
                      <Button minimal onClick={() => { setKeyProvider(keyOpen ? null : company.provider); setApiKey(""); }}>
                        {keyOpen ? "키 입력 닫기" : "API 키"}
                      </Button>
                    </div>
                    {keyOpen && (
                      <div className="first-run-key">
                        <InputGroup
                          type="password"
                          placeholder="API 키"
                          value={apiKey}
                          onChange={(event) => setApiKey(event.target.value)}
                          aria-label={`${company.label} API 키`}
                        />
                        <Button
                          intent="primary"
                          disabled={!apiKey}
                          loading={connect.isPending}
                          onClick={() => {
                            connect.mutate({ provider: company.provider, api_key: apiKey });
                          }}
                        >
                          키 등록
                        </Button>
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
            {connect.isSuccess && <Callout compact role="status">{credentialResultMessage(connect.data?.value)}
              {guideUrl && <p><a href={guideUrl} target="_blank" rel="noopener noreferrer">키 발급 사이트 열기</a></p>}</Callout>}
            {connect.error && <Callout compact intent="danger">{connect.error.message}</Callout>}
            {listed.error && <Callout compact intent="warning">계정 연결 방법을 확인하지 못했습니다. 연결 상태를 다시 확인하세요.</Callout>}
            <Button small minimal icon="refresh" loading={ready.isFetching || listed.isFetching}
              onClick={() => { void ready.refetch(); void listed.refetch(); }}>연결 상태 다시 확인</Button>
            <div className="first-run-footer">
              <span>연결이 끝나면 인터넷 사용 여부를 고릅니다.</span>
              <Button intent="primary" disabled={!modelConnected} onClick={() => setStep("internet")}>
                다음
              </Button>
            </div>
          </>
        ) : (
          <>
            <h1 id="first-run-title">공개 웹을 이 워크스페이스에서 쓸까요?</h1>
            <p className="muted">
              {hosted
                ? value?.disclosure ?? "질문과 분석에 쓰인 자료는 운영자 OpenAI API로 전달됩니다. 공개 웹 허용은 자료 조회만 해당하고, 모델 호출과는 별개입니다."
                : "모델 호출과 별개입니다. 지금 허용해도 프로젝트가 자동으로 켜지지 않습니다. 나중에 그 프로젝트에서 사이트를 등록한 뒤에만 웹을 찾습니다."}
            </p>
            {hosted && value?.model_connected === false && (
              <Callout compact intent="warning">운영자 모델이 아직 준비되지 않았습니다. 키를 입력하지 마세요.</Callout>
            )}
            <div className="first-run-choices">
              <Button
                fill
                alignText="left"
                icon="globe"
                className={`first-run-choice${consent === "ALLOWED" ? " selected" : ""}`}
                onClick={() => update.mutate("ALLOWED")}
              >
                <span className="first-run-choice-copy"><strong>허용</strong><span>연결한 파일 다음에, 프로젝트에서 등록한 사이트만 찾습니다. 지금 허용해도 자동 검색은 없습니다.</span></span>
              </Button>
              <Button
                fill
                alignText="left"
                icon="disable"
                className={`first-run-choice${consent === "DENIED" ? " selected" : ""}`}
                onClick={() => update.mutate("DENIED")}
              >
                <span className="first-run-choice-copy"><strong>거부</strong><span>
                  {hosted
                    ? "웹 자료 조회만 거부합니다. 질문과 분석은 계속 운영자 모델로 전달됩니다."
                    : "웹 자료 조회만 거부합니다. 모델 호출은 계속됩니다."}
                </span></span>
              </Button>
            </div>
            {update.error && <Callout compact intent="danger">{update.error.message}</Callout>}
            <div className="first-run-footer">
              {hosted ? <span>로그인이나 API 키는 넣지 않습니다.</span> : <Button minimal onClick={() => setStep("model")}>모델로 돌아가기</Button>}
              {setupReady ? (
                <Button intent="primary" onClick={onDone}>프로젝트로</Button>
              ) : (
                <span>허용 또는 거부를 고르면 프로젝트로 갑니다.</span>
              )}
            </div>
          </>
        )}
      </section>
    </main>
  );
}
