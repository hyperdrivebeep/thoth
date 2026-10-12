import { getLocale, t, uiText } from "./locale";

/** Adapter for application-owned v1 activity wording. Never use on source or model content. */
export function activityDisplay(text: string | null | undefined, fallback = t("activity.legacyDetail")): string | null {
  if (!text) return null;
  if (getLocale() === "ko") return text;
  const known = uiText(text);
  if (known !== text || !/[가-힣]/.test(text)) return known;
  const patterns: [RegExp, (match: RegExpExecArray) => string][] = [
    [/^연결된 자료(.*)을 읽었습니다\.$/, m => `Read ${t("activity.sourceFallback")}${m[1]}.`],
    [/^(.+)을 읽었습니다\.$/, m => `Read ${m[1]}.`],
    [/^가설 (\d+)개 중 (\d+)개를 검토했습니다\.$/, m => `Reviewed ${m[2]} of ${m[1]} hypotheses.`],
    [/^지지 관계가 기록된 근거 (\d+)곳을 확인했습니다\.$/, m => `Checked ${m[1]} evidence locations with recorded supporting relationships.`],
    [/^반례 관계가 기록된 근거 (\d+)곳을 확인했습니다\.$/, m => `Checked ${m[1]} evidence locations with recorded counterevidence relationships.`],
    [/^판정이 끝나지 않은 근거 (\d+)곳을 보류했습니다\.$/, m => `Held ${m[1]} evidence locations whose assessment is incomplete.`],
    [/^근거 후보 (\d+)곳이 결과 범위에 연결되었습니다\.$/, m => `${m[1]} evidence candidates were linked to the result scope.`],
    [/^현재성이 달라진 자료 (\d+)곳은 다시 확인해야 합니다\.$/, m => `${m[1]} sources with changed currentness need rechecking.`],
    [/^접근 범위를 벗어난 자료 (\d+)곳은 사용하지 않았습니다\.$/, m => `${m[1]} sources outside the access scope were not used.`],
    [/^원격 종료 확인 상태는 (.+)입니다\.$/, m => `Remote termination observation: ${m[1]}.`],
  ];
  for (const [pattern, format] of patterns) {
    const match = pattern.exec(text);
    if (match) return format(match);
  }
  return fallback;
}

export const activityAction: Record<string, string> = {
  search: "Source search", open: "Open source", fetch: "Retrieve source", parse: "Parse content", read: "Read source",
  screen: "Review relevance", select_candidate: "Select evidence candidates", adopt_support: "Adopt supporting evidence",
  adopt_counter: "Adopt counterevidence", run: "Execute action", call_model: "Model call", retry: "Retry",
  cancel: "Cancellation", hold: "Hold", stage_complete: "Research stage",
};
