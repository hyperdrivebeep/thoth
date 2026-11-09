import { bilingualRecord } from "./bilingualRecord";
const korean = {
  UNTESTED: { label: "미시험", hint: "이 기준에 연결된 시험은 있지만 아직 결과가 없습니다." },
  UNLINKED: { label: "미연결", hint: "이 기준에 연결된 시험이 없어 결과가 들어올 곳이 없습니다." },
  UNIT: { label: "단위 오류", hint: "결과의 단위가 기준의 단위와 달라 비교하지 않았습니다." },
  DENOMINATOR: { label: "분모 오류", hint: "결과의 분모가 0 이하이거나, 값이 분자·분모로 계산한 값과 달라 비교하지 않았습니다." },
  VALUE: { label: "값 오류", hint: "결과의 값이 비었거나 숫자가 아니거나 너무 커서 비교하지 않았습니다." },
  CONDITION_MISMATCH: { label: "조건 불일치", hint: "결과는 있지만 기준의 조건과 달라 쓰지 않았습니다." },
  NO_RULE: { label: "규칙 없음", hint: "이 기준에는 판정 규칙이 없어 비교하지 않았습니다." },
  VALID_MISS: { label: "유효한 미달", hint: "결과를 비교할 수 있었고 기준에 못 미쳤습니다." },
  REQUIRED_INCOMPLETE: { label: "필수 기준 미완료", hint: "필수 기준이 없거나, 필수 기준 중 아직 충족이나 미달이 정해지지 않은 것이 있습니다." },
  BASIS_CHANGED: { label: "근거 변경", hint: "이 판정이 기댄 규칙·결과·연결이 바뀌어 다시 계산해야 합니다." },
};
const english: Record<keyof typeof korean, {label: string; hint: string}> = {
  "UNTESTED": {
    "label": "Not tested",
    "hint": "A test is linked to this criterion, but it has no result yet."
  },
  "UNLINKED": {
    "label": "Not linked",
    "hint": "No test is linked to this criterion to receive a result."
  },
  "UNIT": {
    "label": "Unit error",
    "hint": "No comparison was made because the result and criterion units differ."
  },
  "DENOMINATOR": {
    "label": "Denominator error",
    "hint": "No comparison was made because the denominator is nonpositive or the value differs from the numerator/denominator calculation."
  },
  "VALUE": {
    "label": "Value error",
    "hint": "No comparison was made because the value is missing, nonnumeric, or too large."
  },
  "CONDITION_MISMATCH": {
    "label": "Condition mismatch",
    "hint": "A result exists but was not used because its conditions differ."
  },
  "NO_RULE": {
    "label": "No rule",
    "hint": "No comparison was made because this criterion has no assessment rule."
  },
  "VALID_MISS": {
    "label": "Valid result below criterion",
    "hint": "The result was comparable and did not meet the criterion."
  },
  "REQUIRED_INCOMPLETE": {
    "label": "Required criteria incomplete",
    "hint": "Required criteria are absent, or some have not yet been determined as met or unmet."
  },
  "BASIS_CHANGED": {
    "label": "Basis changed",
    "hint": "Rules, results, or links used by this assessment changed; recomputation is needed."
  }
};
const chinese: Record<keyof typeof korean, { label: string; hint: string }> = {
  UNTESTED: { label: "未测试", hint: "已有测试与此标准关联，但还没有结果。" },
  UNLINKED: { label: "未关联", hint: "没有测试与此标准关联，因此没有地方接收结果。" },
  UNIT: { label: "单位错误", hint: "结果的单位与标准的单位不同，因此没有进行比较。" },
  DENOMINATOR: { label: "分母错误", hint: "结果的分母不大于 0，或其值与按分子/分母计算的值不一致，因此没有进行比较。" },
  VALUE: { label: "值错误", hint: "结果的值为空、不是数字或过大，因此没有进行比较。" },
  CONDITION_MISMATCH: { label: "条件不一致", hint: "有结果，但与标准的条件不同，因此没有采用。" },
  NO_RULE: { label: "没有规则", hint: "此标准没有评估规则，因此没有进行比较。" },
  VALID_MISS: { label: "有效但未达标", hint: "结果可以比较，并且没有达到标准。" },
  REQUIRED_INCOMPLETE: { label: "必要标准未完成", hint: "没有必要标准，或必要标准中还有尚未判定为满足或未满足的。" },
  BASIS_CHANGED: { label: "依据已变化", hint: "此评估所依据的规则、结果或链接已变化，需要重新计算。" },
};

const traditional: Record<keyof typeof korean, { label: string; hint: string }> = {
  UNTESTED: { label: "未測試", hint: "已有測試與此標準關聯，但還沒有結果。" },
  UNLINKED: { label: "未關聯", hint: "沒有測試與此標準關聯，因此沒有地方接收結果。" },
  UNIT: { label: "單位錯誤", hint: "結果的單位與標準的單位不同，因此沒有進行比較。" },
  DENOMINATOR: { label: "分母錯誤", hint: "結果的分母不大於 0，或其值與按分子/分母計算的值不一致，因此沒有進行比較。" },
  VALUE: { label: "值錯誤", hint: "結果的值為空、不是數字或過大，因此沒有進行比較。" },
  CONDITION_MISMATCH: { label: "條件不一致", hint: "有結果，但與標準的條件不同，因此沒有采用。" },
  NO_RULE: { label: "沒有規則", hint: "此標準沒有評估規則，因此沒有進行比較。" },
  VALID_MISS: { label: "有效但未達標", hint: "結果可以比較，並且沒有達到標準。" },
  REQUIRED_INCOMPLETE: { label: "必要標準未完成", hint: "沒有必要標準，或必要標準中還有尚未判定為滿足或未滿足的。" },
  BASIS_CHANGED: { label: "依據已變化", hint: "此評估所依據的規則、結果或連結已變化，需要重新計算。" },
};
const japanese: Record<keyof typeof korean, { label: string; hint: string }> = {
  UNTESTED: { label: "未テスト", hint: "この基準にテストが結びついていますが、まだ結果がありません。" },
  UNLINKED: { label: "未リンク", hint: "この基準に結びついたテストがなく、結果の入る場所がありません。" },
  UNIT: { label: "単位のエラー", hint: "結果の単位が基準の単位と異なるため、比較しませんでした。" },
  DENOMINATOR: { label: "分母のエラー", hint: "結果の分母が0以下か、値が分子・分母から計算した値と異なるため、比較しませんでした。" },
  VALUE: { label: "値のエラー", hint: "結果の値が空か、数値でないか、大きすぎるため、比較しませんでした。" },
  CONDITION_MISMATCH: { label: "条件の不一致", hint: "結果はありますが、基準の条件と異なるため、使いませんでした。" },
  NO_RULE: { label: "ルールなし", hint: "この基準には判定ルールがないため、比較しませんでした。" },
  VALID_MISS: { label: "有効だが未達", hint: "結果を比較でき、基準に届きませんでした。" },
  REQUIRED_INCOMPLETE: { label: "必須の基準が未完了", hint: "必須の基準がないか、必須の基準のうち、満たすか満たさないかがまだ決まっていないものがあります。" },
  BASIS_CHANGED: { label: "根拠の変更", hint: "この判定が拠りどころにしたルール・結果・リンクが変わったため、再計算が必要です。" },
};
export const traceReasonText = bilingualRecord(korean, english, chinese, traditional, japanese);
