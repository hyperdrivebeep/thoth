import { Button, Callout } from "@blueprintjs/core";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { rpc } from "../api/rpcClient";
import { connectionErrorText, reasonCode } from "./connectionCopy";

/** npm may run for five minutes; the server stops it and answers with a typed reason. */
const INSTALL_TIMEOUT_MS = 330_000;

/**
 * Install one THOTH-owned helper tool from the screen. The command stays visible for the
 * cases the automatic install cannot cover (no Node.js, no network).
 */
export function ToolInstallHelp({ toolId, title, intro, command, onInstalled }: {
  toolId: string; title: string; intro: ReactNode; command: string; onInstalled?: () => void;
}) {
  const client = useQueryClient();
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<{ text: string; code: string | null } | null>(null);
  const [copied, setCopied] = useState(false);
  const active = useRef(true);
  const busy = useRef(false);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);

  const install = async () => {
    if (busy.current) return;
    busy.current = true; setRunning(true); setError(null);
    try {
      await rpc("model/tooling/install", { project_id: "system:workspace", tool_id: toolId },
        crypto.randomUUID(), undefined, { timeoutMs: INSTALL_TIMEOUT_MS });
      await Promise.allSettled([
        client.invalidateQueries({ queryKey: ["model-credentials"] }),
        client.invalidateQueries({ queryKey: ["workspace-ready"] }),
        client.invalidateQueries({ queryKey: ["model-settings"] }),
      ]);
      onInstalled?.();
    } catch (caught) {
      if (active.current) setError({ code: reasonCode(caught),
        text: connectionErrorText(caught, "자동 설치에 실패했습니다. 아래 명령을 직접 실행하세요.") });
    } finally {
      busy.current = false;
      if (active.current) setRunning(false);
    }
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(command); setCopied(true); }
    catch { setCopied(false); }
  };
  return <div className="codex-tool-help">
    <p><strong>{title}</strong></p>
    <p>{intro}</p>
    <Button small intent="primary" icon="download" loading={running} disabled={running} onClick={() => void install()}>
      {running ? "설치 중…" : "자동 설치"}</Button>
    {running && <p role="status">설치 중입니다. 최대 5분 걸릴 수 있습니다. 이 화면을 닫지 마세요.</p>}
    {error && <Callout compact intent="warning" role="alert">{error.text}{error.code && <small> 오류 코드: {error.code}</small>}</Callout>}
    <p>직접 설치하려면 PowerShell에서 아래 명령을 실행하세요.</p>
    <pre><code>{command}</code></pre>
    <Button small icon="duplicate" onClick={() => void copy()}>{copied ? "복사됨" : "명령 복사"}</Button>
  </div>;
}
