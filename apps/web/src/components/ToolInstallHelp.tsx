import { message } from "../i18n/formatMessages";
import { t } from "../i18n/locale";
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
        text: connectionErrorText(caught, t("tools.installFailed")) });
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
      {running ? t("tools.installing") : t("tools.install")}</Button>
    {running && <p role="status">{t("tools.wait")}</p>}
    {error && <Callout compact intent="warning" role="alert">{error.text}{error.code && <small>{message("errorCode", error.code)}</small>}</Callout>}
    <p>{t("tools.manual")}</p>
    <pre><code>{command}</code></pre>
    <Button small icon="duplicate" onClick={() => void copy()}>{copied ? t("tools.copied") : t("tools.copy")}</Button>
  </div>;
}
