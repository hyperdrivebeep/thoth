import { t } from "../i18n/locale";
import { CODEX_TOOL_INSTALL_COMMAND } from "./connectionCopy";
import { ToolInstallHelp } from "./ToolInstallHelp";

/** Shown only after the server reports a missing or mismatched pinned Codex tool. */
export function CodexToolHelp({ onInstalled }: { onInstalled?: () => void }) {
  return <ToolInstallHelp toolId="codex" title={t("tools.codexTitle")} command={CODEX_TOOL_INSTALL_COMMAND}
    intro={t("tools.codexNotice")}
    onInstalled={onInstalled} />;
}
