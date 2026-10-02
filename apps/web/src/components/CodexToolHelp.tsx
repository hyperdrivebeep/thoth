import { CODEX_TOOL_INSTALL_COMMAND } from "./connectionCopy";
import { ToolInstallHelp } from "./ToolInstallHelp";

/** Shown only after the server reports a missing or mismatched pinned Codex tool. */
export function CodexToolHelp({ onInstalled }: { onInstalled?: () => void }) {
  return <ToolInstallHelp toolId="codex" title="Codex 연결 도구 설치" command={CODEX_TOOL_INSTALL_COMMAND}
    intro="OpenAI 공식 Codex CLI 0.157.1을 THOTH 전용 폴더에 설치합니다. 기존 Codex 설치와 로그인은 바꾸지 않습니다."
    onInstalled={onInstalled} />;
}
