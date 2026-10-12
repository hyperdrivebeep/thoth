import { t } from "../i18n/locale";
import { CLAUDE_CODE_INSTALL_COMMAND } from "./connectionCopy";
import { ToolInstallHelp } from "./ToolInstallHelp";

/** Shown when the official Claude Code executable is missing or too old. */
export function ClaudeCodeHelp({ onInstalled }: { onInstalled?: () => void }) {
  return <ToolInstallHelp toolId="claude-code" title={t("tools.claudeTitle")} command={CLAUDE_CODE_INSTALL_COMMAND}
    intro={t("tools.claudeNotice")}
    onInstalled={onInstalled} />;
}
