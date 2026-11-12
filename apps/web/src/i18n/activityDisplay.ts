import { getLocale, t, uiText } from "./locale";
import type { LanguageCode } from "./languageChoices";

type Pattern = [RegExp, (match: RegExpExecArray) => string];

/** The Korean wording of each v1 activity sentence is matched and rewritten into the other screen languages. */
const patterns: Record<Exclude<LanguageCode, "ko">, Pattern[]> = {
  en: [
    [/^연결된 자료(.*)을 읽었습니다\.$/, (m) => `Read ${t("activity.sourceFallback")}${m[1]}.`],
    [/^(.+)을 읽었습니다\.$/, (m) => `Read ${m[1]}.`],
    [
      /^가설 (\d+)개 중 (\d+)개를 검토했습니다\.$/,
      (m) => `Reviewed ${m[2]} of ${m[1]} hypotheses.`,
    ],
    [
      /^지지 관계가 기록된 근거 (\d+)곳을 확인했습니다\.$/,
      (m) => `Checked ${m[1]} evidence locations with recorded supporting relationships.`,
    ],
    [
      /^반례 관계가 기록된 근거 (\d+)곳을 확인했습니다\.$/,
      (m) => `Checked ${m[1]} evidence locations with recorded counterevidence relationships.`,
    ],
    [
      /^판정이 끝나지 않은 근거 (\d+)곳을 보류했습니다\.$/,
      (m) => `Held ${m[1]} evidence locations whose assessment is incomplete.`,
    ],
    [
      /^근거 후보 (\d+)곳이 결과 범위에 연결되었습니다\.$/,
      (m) => `${m[1]} evidence candidates were linked to the result scope.`,
    ],
    [
      /^(?:현재성이|최신 상태가) 달라진 자료 (\d+)곳은 다시 확인해야 합니다\.$/,
      (m) => `${m[1]} sources with changed currentness need rechecking.`,
    ],
    [
      /^접근 범위를 벗어난 자료 (\d+)곳은 사용하지 않았습니다\.$/,
      (m) => `${m[1]} sources outside the access scope were not used.`,
    ],
    [/^원격 종료 확인 상태는 (.+)입니다\.$/, (m) => `Remote termination observation: ${m[1]}.`],
  ],
  "zh-Hans": [
    [/^연결된 자료(.*)을 읽었습니다\.$/, (m) => `已读取${t("activity.sourceFallback")}${m[1]}。`],
    [/^(.+)을 읽었습니다\.$/, (m) => `已读取${m[1]}。`],
    [/^가설 (\d+)개 중 (\d+)개를 검토했습니다\.$/, (m) => `已审查 ${m[1]} 个假设中的 ${m[2]} 个。`],
    [
      /^지지 관계가 기록된 근거 (\d+)곳을 확인했습니다\.$/,
      (m) => `已检查 ${m[1]} 处记录了支持关系的证据位置。`,
    ],
    [
      /^반례 관계가 기록된 근거 (\d+)곳을 확인했습니다\.$/,
      (m) => `已检查 ${m[1]} 处记录了反面证据关系的证据位置。`,
    ],
    [
      /^판정이 끝나지 않은 근거 (\d+)곳을 보류했습니다\.$/,
      (m) => `已搁置 ${m[1]} 处评估尚未完成的证据位置。`,
    ],
    [
      /^근거 후보 (\d+)곳이 결과 범위에 연결되었습니다\.$/,
      (m) => `${m[1]} 处候选证据已关联到结果范围。`,
    ],
    [
      /^(?:현재성이|최신 상태가) 달라진 자료 (\d+)곳은 다시 확인해야 합니다\.$/,
      (m) => `${m[1]} 处时效性已变化的资料需要重新检查。`,
    ],
    [
      /^접근 범위를 벗어난 자료 (\d+)곳은 사용하지 않았습니다\.$/,
      (m) => `${m[1]} 处超出访问范围的资料没有被使用。`,
    ],
    [/^원격 종료 확인 상태는 (.+)입니다\.$/, (m) => `远程终止的确认状态：${m[1]}。`],
  ],
  "zh-Hant": [
    [/^연결된 자료(.*)을 읽었습니다\.$/, (m) => `已讀取${t("activity.sourceFallback")}${m[1]}。`],
    [/^(.+)을 읽었습니다\.$/, (m) => `已讀取${m[1]}。`],
    [/^가설 (\d+)개 중 (\d+)개를 검토했습니다\.$/, (m) => `已審查 ${m[1]} 個假設中的 ${m[2]} 個。`],
    [
      /^지지 관계가 기록된 근거 (\d+)곳을 확인했습니다\.$/,
      (m) => `已檢查 ${m[1]} 處記錄了支持關係的證據位置。`,
    ],
    [
      /^반례 관계가 기록된 근거 (\d+)곳을 확인했습니다\.$/,
      (m) => `已檢查 ${m[1]} 處記錄了反面證據關係的證據位置。`,
    ],
    [
      /^판정이 끝나지 않은 근거 (\d+)곳을 보류했습니다\.$/,
      (m) => `已擱置 ${m[1]} 處評估尚未完成的證據位置。`,
    ],
    [
      /^근거 후보 (\d+)곳이 결과 범위에 연결되었습니다\.$/,
      (m) => `${m[1]} 處候選證據已關聯到結果範圍。`,
    ],
    [
      /^(?:현재성이|최신 상태가) 달라진 자료 (\d+)곳은 다시 확인해야 합니다\.$/,
      (m) => `${m[1]} 處時效性已變化的資料需要重新檢查。`,
    ],
    [
      /^접근 범위를 벗어난 자료 (\d+)곳은 사용하지 않았습니다\.$/,
      (m) => `${m[1]} 處超出存取範圍的資料沒有被使用。`,
    ],
    [/^원격 종료 확인 상태는 (.+)입니다\.$/, (m) => `遠端終止的確認狀態：${m[1]}。`],
  ],
  ja: [
    [
      /^연결된 자료(.*)을 읽었습니다\.$/,
      (m) => `${t("activity.sourceFallback")}${m[1]}を読み込みました。`,
    ],
    [/^(.+)을 읽었습니다\.$/, (m) => `${m[1]}を読み込みました。`],
    [
      /^가설 (\d+)개 중 (\d+)개를 검토했습니다\.$/,
      (m) => `仮説 ${m[1]} 件のうち ${m[2]} 件をレビューしました。`,
    ],
    [
      /^지지 관계가 기록된 근거 (\d+)곳을 확인했습니다\.$/,
      (m) => `裏づけの関係が記録された根拠の位置 ${m[1]} か所を確認しました。`,
    ],
    [
      /^반례 관계가 기록된 근거 (\d+)곳을 확인했습니다\.$/,
      (m) => `反証の関係が記録された根拠の位置 ${m[1]} か所を確認しました。`,
    ],
    [
      /^판정이 끝나지 않은 근거 (\d+)곳을 보류했습니다\.$/,
      (m) => `判定が終わっていない根拠の位置 ${m[1]} か所を保留にしました。`,
    ],
    [
      /^근거 후보 (\d+)곳이 결과 범위에 연결되었습니다\.$/,
      (m) => `根拠の候補 ${m[1]} か所を、結果の範囲に結びつけました。`,
    ],
    [
      /^(?:현재성이|최신 상태가) 달라진 자료 (\d+)곳은 다시 확인해야 합니다\.$/,
      (m) => `最新性が変わった資料 ${m[1]} か所は、もう一度確認する必要があります。`,
    ],
    [
      /^접근 범위를 벗어난 자료 (\d+)곳은 사용하지 않았습니다\.$/,
      (m) => `アクセス範囲の外にある資料 ${m[1]} か所は、使いませんでした。`,
    ],
    [/^원격 종료 확인 상태는 (.+)입니다\.$/, (m) => `リモートでの終了の確認状態：${m[1]}。`],
  ],
};

/** Adapter for application-owned v1 activity wording. Never use on source or model content. */
export function activityDisplay(
  text: string | null | undefined,
  fallback = t("activity.legacyDetail"),
): string | null {
  if (!text) return null;
  const locale = getLocale();
  if (locale === "ko") return text;
  const known = uiText(text);
  if (known !== text || !/[가-힣]/.test(text)) return known;
  for (const [pattern, format] of patterns[locale]) {
    const match = pattern.exec(text);
    if (match) return format(match);
  }
  return fallback;
}

const actionLabels: Record<Exclude<LanguageCode, "ko">, Record<string, string>> = {
  en: {
    search: "Source search",
    open: "Open source",
    fetch: "Retrieve source",
    parse: "Parse content",
    read: "Read source",
    screen: "Review relevance",
    select_candidate: "Select evidence candidates",
    adopt_support: "Adopt supporting evidence",
    adopt_counter: "Adopt counterevidence",
    run: "Execute action",
    call_model: "Model call",
    retry: "Retry",
    cancel: "Cancellation",
    hold: "Hold",
    stage_complete: "Research stage",
  },
  "zh-Hans": {
    search: "搜索资料",
    open: "打开资料",
    fetch: "获取资料",
    parse: "解析内容",
    read: "读取资料",
    screen: "审查相关性",
    select_candidate: "选出候选证据",
    adopt_support: "采纳支持证据",
    adopt_counter: "采纳反面证据",
    run: "执行行动",
    call_model: "模型调用",
    retry: "重试",
    cancel: "取消",
    hold: "暂缓",
    stage_complete: "研究阶段",
  },
  "zh-Hant": {
    search: "搜尋資料",
    open: "開啟資料",
    fetch: "獲取資料",
    parse: "解析內容",
    read: "讀取資料",
    screen: "審查相關性",
    select_candidate: "選出候選證據",
    adopt_support: "採納支援證據",
    adopt_counter: "採納反面證據",
    run: "執行行動",
    call_model: "模型呼叫",
    retry: "重試",
    cancel: "取消",
    hold: "暫緩",
    stage_complete: "研究階段",
  },
  ja: {
    search: "資料を検索",
    open: "資料を開く",
    fetch: "資料を取得",
    parse: "内容を解析",
    read: "資料を読み込む",
    screen: "関連性をレビュー",
    select_candidate: "根拠の候補を選ぶ",
    adopt_support: "裏づける根拠を採用",
    adopt_counter: "反証の根拠を採用",
    run: "行動を実行",
    call_model: "モデル呼び出し",
    retry: "再試行",
    cancel: "キャンセル",
    hold: "保留",
    stage_complete: "調査の段階",
  },
};
const researchActivity: Record<Exclude<LanguageCode, "ko">, string> = {
  en: "Research activity",
  "zh-Hans": "研究活动",
  "zh-Hant": "研究活動",
  ja: "調査の活動",
};

/** The label of an activity action in a language other than Korean (Korean shows the server's own wording). */
export function activityActionLabel(action: string): string {
  const locale = getLocale();
  return locale === "ko" ? "" : (actionLabels[locale][action] ?? researchActivity[locale]);
}
