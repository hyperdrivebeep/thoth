import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it } from "vitest";
import { setLocale, t } from "../i18n/locale";
import { interruptionView } from "./resultInterruption";
import { ResultInterruptionNotice } from "./ResultInterruptionNotice";

afterEach(() => setLocale("ko"));

const STALLED = "OAUTH_STALLED_STREAM_REMOTE_STOP_UNKNOWN";
const DEADLINE = "OAUTH_DISPATCH_DEADLINE_REMOTE_STOP_UNKNOWN";

/** A SUCCEEDED run whose research ended on a model-call reason, with or without a saved answer. */
function notice(code: string, answer: string | null): string {
  const result: Record<string, unknown> =
    answer === null ? { terminal_reason: code } : { answer, terminal_reason: code };
  const view = interruptionView({ state: "SUCCEEDED", result, terminalReason: code });
  return renderToStaticMarkup(
    <ResultInterruptionNotice
      view={view}
      result={result}
      hasSavedResult={false}
      onDetail={() => {}}
    />,
  );
}

it("says the answer was saved when the connection was cut after a partial answer was stored", () => {
  for (const code of [STALLED, DEADLINE]) {
    const html = notice(code, "复核暂缓 · 部分草稿：事实");
    expect(html).toContain(t("result.stalled"));
    expect(html).not.toContain(t("result.remoteUnknown"));
    expect(html).not.toContain(t("result.timeLimit"));
  }
  expect(t("result.stalled")).toBe(
    "답은 저장됐지만 마지막 확인 전에 연결이 끊겼습니다. 모델 쪽에서는 계속 처리했을 수 있습니다.",
  );
});

it("keeps today's wording when the cut-off call left no answer", () => {
  const stalled = notice(STALLED, null);
  expect(stalled).not.toContain(t("result.stalled"));
  expect(stalled).toContain(t("result.remoteUnknown"));
  expect(stalled).toContain(t("result.slow"));
  const deadline = notice(DEADLINE, "  ");
  expect(deadline).not.toContain(t("result.stalled"));
  expect(deadline).toContain(t("result.timeLimit"));
  expect(deadline).toContain(t("result.remoteUnknown"));
});

it("does not call the answer saved when only other findings were kept", () => {
  const result = { terminal_reason: STALLED, portfolio: { hypotheses: [{ hypothesis_id: "h" }] } };
  const view = interruptionView({ state: "SUCCEEDED", result, terminalReason: STALLED });
  expect(view.content).toBe(true);
  expect(view.hasAnswer).toBe(false);
  const html = renderToStaticMarkup(
    <ResultInterruptionNotice
      view={view}
      result={result}
      hasSavedResult={false}
      onDetail={() => {}}
    />,
  );
  expect(html).not.toContain(t("result.stalled"));
  expect(html).toContain(t("result.remoteUnknown"));
});

it("says the model ended with an error for an incomplete response, in place of the unknown-stop line", () => {
  for (const answer of [null, "부분 답"]) {
    const html = notice("OAUTH_INCOMPLETE_RESPONSE", answer);
    expect(html).toContain(t("result.incompleteResponse"));
    expect(html).not.toContain(t("result.remoteUnknown"));
    expect(html).not.toContain(t("result.stalled"));
  }
  expect(t("result.incompleteResponse")).toBe("모델 쪽 오류로 끝났습니다.");
});

it("leaves other model-call reasons as they were and compares codes exactly", () => {
  const transport = notice("OAUTH_TRANSPORT_FAILURE", "부분 답");
  expect(transport).toContain(t("result.remoteUnknown"));
  expect(transport).not.toContain(t("result.stalled"));
  expect(transport).not.toContain(t("result.incompleteResponse"));
  for (const near of ["OAUTH_INCOMPLETE_RESPONSE_X", "X_" + STALLED]) {
    const html = notice(near, "부분 답");
    expect(html).not.toContain(t("result.incompleteResponse"));
    expect(html).not.toContain(t("result.stalled"));
  }
  expect(
    renderToStaticMarkup(
      <ResultInterruptionNotice
        view={interruptionView({
          state: "SUCCEEDED",
          result: { answer: "a" },
          terminalReason: "BOUNDED_RESEARCH_COMPLETE",
        })}
        result={{ answer: "a" }}
        hasSavedResult={false}
        onDetail={() => {}}
      />,
    ),
  ).toBe("");
});

it("replaces the headline only for a cut-off call after a saved answer", () => {
  const cases: Array<[string, string | null, boolean]> = [
    [STALLED, "부분 답", true],
    [DEADLINE, "부분 답", true],
    [STALLED, null, false],
    [DEADLINE, "  ", false],
    ["OAUTH_INCOMPLETE_RESPONSE", "부분 답", false],
    ["OAUTH_TRANSPORT_FAILURE", "부분 답", false],
    ["TOTAL_RESEARCH_BUDGET_EXHAUSTED", null, false],
  ];
  for (const [code, answer, saved] of cases) {
    const html = notice(code, answer);
    expect(html.includes(t("result.savedBeforeCut")), code + " " + answer).toBe(saved);
    expect(html.includes(t("result.incomplete")), code + " " + answer).toBe(!saved);
  }
  expect(t("result.savedBeforeCut")).toBe("연구 답은 저장됐지만 끝까지 확인하지 못했습니다.");
});

it("has the headline in every screen language", () => {
  const seen = new Set<string>();
  for (const locale of ["ko", "en", "ja", "zh-Hans"] as const) {
    setLocale(locale);
    expect(notice(STALLED, "answer")).toContain(t("result.savedBeforeCut"));
    expect(notice(STALLED, "answer")).not.toContain(t("result.incomplete"));
    seen.add(t("result.savedBeforeCut"));
  }
  expect(seen.size).toBe(4);
  setLocale("en");
  expect(t("result.savedBeforeCut")).toBe(
    "The research answer was saved, but it could not be checked to the end.",
  );
});

it("has the two lines in every screen language", () => {
  const seen = new Set<string>();
  for (const locale of ["ko", "en", "ja", "zh-Hans"] as const) {
    setLocale(locale);
    const stalled = notice(STALLED, "answer");
    const failed = notice("OAUTH_INCOMPLETE_RESPONSE", "answer");
    expect(stalled).toContain(t("result.stalled"));
    expect(failed).toContain(t("result.incompleteResponse"));
    seen.add(t("result.stalled"));
    seen.add(t("result.incompleteResponse"));
  }
  expect(seen.size).toBe(8);
  setLocale("en");
  expect(t("result.stalled")).toBe(
    "The answer was saved, but the connection was cut before the final check. The model may have kept processing.",
  );
  expect(t("result.incompleteResponse")).toBe("The model ended with an error.");
});
const AUTO_OFF = {
  ko: ["끊긴 모델 호출 자동 다시 시도가 꺼져 있습니다.", "설정 열기"],
  en: ["Automatic retry of an interrupted model call is turned off.", "Open settings"],
  "zh-Hans": ["被中断的模型调用的自动重试已关闭。", "打开设置"],
  "zh-Hant": ["被中斷的模型呼叫的自動重試已關閉。", "開啟設定"],
  ja: ["中断されたモデル呼び出しの自動再試行はオフになっています。", "設定を開く"],
} as const;

function withRetry(
  code: string,
  answer: string | null,
  retry: { autoRetryOff?: boolean; onOpenSettings?: () => void },
  state = "SUCCEEDED",
): string {
  const result: Record<string, unknown> =
    answer === null ? { terminal_reason: code } : { answer, terminal_reason: code };
  const view = interruptionView({ state, result, terminalReason: code });
  return renderToStaticMarkup(
    <ResultInterruptionNotice
      view={view}
      result={result}
      hasSavedResult={false}
      onDetail={() => {}}
      {...retry}
    />,
  );
}

it("says automatic retry is off, with a settings button, only for a cut-off model call and only when it is off", () => {
  const open = () => {};
  for (const code of [
    STALLED,
    DEADLINE,
    "OAUTH_TRANSPORT_FAILURE",
    "OAUTH_RUNAWAY_OUTPUT",
    "OAUTH_STALLED_STREAM",
  ]) {
    for (const answer of ["부분 답", null]) {
      const html = withRetry(code, answer, { autoRetryOff: true, onOpenSettings: open });
      expect(html, code + answer).toContain(t("result.autoRetryOff"));
      expect(html, code + answer).toContain(t("result.autoRetryOpenSettings"));
      const quiet = withRetry(code, answer, { autoRetryOff: false, onOpenSettings: open });
      expect(quiet, code + answer).not.toContain(t("result.autoRetryOff"));
      expect(quiet, code + answer).not.toContain(t("result.autoRetryOpenSettings"));
    }
  }
  // Other reasons for an unfinished run are not a cut-off call.
  for (const code of [
    "TOTAL_RESEARCH_BUDGET_EXHAUSTED",
    "OAUTH_INCOMPLETE_RESPONSE",
    "OAUTH_REQUEST_REJECTED_429",
  ]) {
    expect(
      withRetry(code, "부분 답", { autoRetryOff: true, onOpenSettings: open }),
      code,
    ).not.toContain(t("result.autoRetryOff"));
  }
  expect(
    withRetry("BOUNDED_RESEARCH_COMPLETE", "답", { autoRetryOff: true, onOpenSettings: open }),
  ).toBe("");
  // Without the way to the settings, the sentence is shown without a button.
  const noButton = withRetry(STALLED, "부분 답", { autoRetryOff: true });
  expect(noButton).toContain(t("result.autoRetryOff"));
  expect(noButton).not.toContain(t("result.autoRetryOpenSettings"));
  // The default is silent.
  expect(withRetry(STALLED, "부분 답", {})).not.toContain(t("result.autoRetryOff"));
});

it("has the retry-off sentence and button in every screen language", () => {
  const seen = new Set<string>();
  for (const [locale, [sentence, button]] of Object.entries(AUTO_OFF)) {
    setLocale(locale as keyof typeof AUTO_OFF);
    expect(t("result.autoRetryOff")).toBe(sentence);
    expect(t("result.autoRetryOpenSettings")).toBe(button);
    const html = withRetry(STALLED, "answer", { autoRetryOff: true, onOpenSettings: () => {} });
    expect(html).toContain(sentence);
    expect(html).toContain(button);
    seen.add(sentence);
  }
  expect(seen.size).toBe(5);
});

/** A research run that ended CANCELLED, with the reason code the server recorded (none for an older record). */
function cancelledNotice(code: string | null): string {
  const view = interruptionView({
    state: "CANCELLED",
    result: null,
    error: code === null ? null : { message: code, data: { reason_code: code } },
  });
  return renderToStaticMarkup(
    <ResultInterruptionNotice
      view={view}
      result={null}
      hasSavedResult={false}
      onDetail={() => {}}
    />,
  );
}

it("says the person stopped it, in the five languages", () => {
  const words = {
    ko: "직접 중지했습니다.",
    en: "You stopped it yourself.",
    "zh-Hans": "你已手动停止。",
    "zh-Hant": "你已手動停止。",
    ja: "ご自身で停止しました。",
  } as const;
  for (const [locale, sentence] of Object.entries(words)) {
    setLocale(locale as keyof typeof words);
    const html = cancelledNotice("USER_STOPPED");
    expect(html).toContain(sentence);
    expect(html).not.toContain(t("result.cancelUnknown"));
    expect(html).not.toContain("USER_STOPPED");
  }
});

it("keeps today's sentence for a cancelled run with no reason on record", () => {
  const html = cancelledNotice(null);
  expect(html).toContain(t("result.cancelUnknown"));
  expect(html).not.toContain("직접 중지했습니다.");
});

it("still shows another recorded reason as the recorded code, never as the person's stop", () => {
  const html = cancelledNotice("RESEARCH_TIME_BUDGET_EXHAUSTED");
  expect(html).toContain("RESEARCH_TIME_BUDGET_EXHAUSTED");
  expect(html).not.toContain("직접 중지했습니다.");
  expect(html).not.toContain(t("result.cancelUnknown"));
});
