import { CLAUDE_CODE_INSTALL_COMMAND } from "./connectionCopy";
import { ToolInstallHelp } from "./ToolInstallHelp";

/** Shown when the official Claude Code executable is missing or too old. */
export function ClaudeCodeHelp({ onInstalled }: { onInstalled?: () => void }) {
  return <ToolInstallHelp toolId="claude-code" title="Claude Code 설치" command={CLAUDE_CODE_INSTALL_COMMAND}
    intro="Anthropic 공식 Claude Code를 THOTH 전용 폴더에 설치합니다. 다른 프로그램이 쓰는 기존 Claude Code 설치는 바꾸지 않습니다."
    onInstalled={onInstalled} />;
}
