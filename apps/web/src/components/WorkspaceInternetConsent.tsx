import { Button, Callout } from "@blueprintjs/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { rpc } from "../api/rpcClient";

const STATE_TEXT: Record<string, string> = { ALLOWED: "허용함", DENIED: "거부함" };

/** The workspace's "public web lookup" consent in the settings page: the same choice and the same RPC as the second
 * step of the first setup, for a workspace that already has projects and no consent saved. */
export function WorkspaceInternetConsent({ consent, focus = false, onFocused }: { consent: string; focus?: boolean; onFocused?: () => void }) {
  const client = useQueryClient();
  const card = useRef<HTMLElement | null>(null);
  const update = useMutation({
    mutationFn: (internet_consent: string) => rpc("workspace/setup/update", { internet_consent }, crypto.randomUUID()),
    onSuccess: async () => {
      await Promise.all([client.invalidateQueries({ queryKey: ["workspace-ready"] }), client.invalidateQueries({ queryKey: ["workspace-setup"] })]);
    },
  });
  useEffect(() => {
    if (!focus) return;
    card.current?.scrollIntoView?.({ block: "center" });
    card.current?.querySelector("button")?.focus();
    onFocused?.();
  }, [focus, onFocused]);
  return <section className="detail-card" id="workspace-internet-consent" ref={card} aria-labelledby="workspace-internet-consent-title">
    <h2 id="workspace-internet-consent-title">공개 웹 자료 조회</h2>
    <p>현재: <strong>{STATE_TEXT[consent] ?? "아직 고르지 않았습니다"}</strong></p>
    <p className="muted">모델 호출과 별개입니다. 지금 허용해도 프로젝트가 자동으로 켜지지 않습니다. 나중에 그 프로젝트에서 사이트를 등록한 뒤에만 웹을 찾습니다.</p>
    <div className="first-run-choices">
      <Button icon="globe" intent={consent === "ALLOWED" ? "primary" : "none"} aria-pressed={consent === "ALLOWED"} loading={update.isPending} onClick={() => update.mutate("ALLOWED")}>허용</Button>
      <Button icon="disable" intent={consent === "DENIED" ? "primary" : "none"} aria-pressed={consent === "DENIED"} loading={update.isPending} onClick={() => update.mutate("DENIED")}>거부</Button>
    </div>
    <p className="muted">허용: 연결한 파일 다음에, 프로젝트에서 등록한 사이트만 찾습니다. 지금 허용해도 자동 검색은 없습니다. 거부: 웹 자료 조회만 거부합니다. 모델 호출은 계속됩니다.</p>
    {update.error && <Callout compact intent="danger" role="alert">{update.error.message}</Callout>}
  </section>;
}
