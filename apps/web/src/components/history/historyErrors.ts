import { RpcError } from "../../api/rpcClient";

export function restoreErrorText(error: Error): string {
  if (error instanceof RpcError) {
    const code = String(error.details.reason_code ?? "");
    if (/HEAD_CHANGED|PREVIEW_STALE/.test(code)) return "미리보기 이후 기록이 바뀌었습니다. 새로 읽고 적용할 내용을 다시 확인하세요.";
    if (/SOURCE_UNAVAILABLE|SOURCE_DRIFT/.test(code)) return "자료의 버전이나 접근 상태가 바뀌어 이 내용은 적용할 수 없습니다.";
    if (/ACCESS|AUTH|ROLE|SCOPE|REENTRY/.test(code)) return "현재 접근 권한으로 이 요청을 확인하거나 적용할 수 없습니다.";
    if (/NOT_READY|SCHEMA_UNSUPPORTED|BLOCKED/.test(code)) return "이 항목의 복원은 아직 지원되지 않거나 적용 조건을 충족하지 못했습니다.";
    if (/IDEMPOTENCY/.test(code)) return "기존 요청과 입력이 일치하지 않습니다. 원 요청을 유지한 채 결과를 확인해야 합니다.";
    return "서버에서 적용 완료를 확인하지 못했습니다. 원 요청의 결과를 확인하세요.";
  }
  if (error.name === "ZodError") return "서버 응답 형식을 확인하지 못했습니다. 적용을 반복하지 말고 원 요청의 결과를 확인하세요.";
  if (error instanceof TypeError || ["AbortError", "TimeoutError"].includes(error.name)) return "연결이 끊기거나 응답이 늦어 적용 여부를 확인하지 못했습니다. 원 요청의 결과를 확인하세요.";
  return error.message;
}
