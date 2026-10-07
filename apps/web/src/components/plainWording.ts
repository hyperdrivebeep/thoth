/** Wording for what the program itself records (the sufficiency reasons and the built-in criteria), in plain Korean.
 *  The recorded text is English and stays as it is; the screen shows these sentences and folds the original away. */

const reasons: Record<string, string> = {
  "decision scope is explicit": "무엇을 판단할지 분명합니다.",
  "decision question is empty": "무엇을 판단할지 질문이 비어 있습니다.",
  "official evaluator-ready criterion exists": "공식 기준이 판정에 쓸 수 있게 갖춰져 있습니다.",
  "criteria exist but official evaluator inputs are incomplete": "기준은 있지만 공식 판정에 필요한 입력이 아직 다 갖춰지지 않았습니다.",
  "no criterion candidate exists": "기준으로 삼을 만한 후보가 없습니다.",
  "verified supporting evidence exists": "확인된 뒷받침 근거가 있습니다.",
  "evidence is extracted but not support-verified": "근거는 뽑았지만 뒷받침하는지 아직 확인하지 않았습니다.",
  "no cutoff-eligible evidence exists": "기준시점 안에서 쓸 수 있는 근거가 없습니다.",
  "comparison conditions are confirmed": "비교 조건이 확인됐습니다.",
  "comparison conditions conflict": "비교 조건이 서로 맞지 않습니다.",
  "comparison conditions have not been confirmed": "비교 조건이 같은지 아직 확인하지 못했습니다.",
  "counterevidence pass is recorded": "반대 근거를 찾아본 기록이 있습니다.",
  "counterevidence has not been checked": "반대 근거는 아직 찾아보지 않았습니다.",
  "measurement implementation is verified": "측정 방식이 올바른지 확인됐습니다.",
  "measurement implementation failed verification": "측정 방식이 올바른지 확인했지만 맞지 않았습니다.",
  "measurement implementation is not verified": "측정 방식이 올바른지 아직 확인하지 못했습니다.",
  "expert-confirmed semantics are bound to an official criterion": "기준의 뜻을 전문가가 확인해 공식 기준에 연결했습니다.",
  "criterion semantics still require expert confirmation": "기준이 정확히 무엇을 뜻하는지 전문가의 확인이 더 필요합니다.",
  "no criterion semantics are available": "기준이 무엇을 뜻하는지 알 수 있는 자료가 없습니다.",
};

/** The Korean sentence for one recorded reason, or null when it is not one the program knows. */
export function plainReason(text: string): string | null {
  return reasons[text.trim()] ?? null;
}

const HANGUL = /[가-힣]/;
const LATIN_WORD = /[A-Za-z]{3,}/;

/** Text that people can read as it is: Korean (or only numbers and signs). English that nobody has worded is set aside. */
export function isReadable(text: string): boolean {
  return HANGUL.test(text) || !LATIN_WORD.test(text);
}

export function splitReadable(items: string[]): { plain: string[]; technical: string[] } {
  const plain: string[] = [];
  const technical: string[] = [];
  for (const item of items) {
    const known = plainReason(item);
    if (known) plain.push(known);
    else if (isReadable(item)) plain.push(item);
    else technical.push(item);
  }
  return { plain, technical };
}

/** The built-in criteria (task profiles) record a short English target and question. */
const builtIn: Record<string, { title: string; question: string; english: string }> = {
  answer: { title: "요청한 대상과 위치", question: "요청한 대상, 항목, 원문 위치와 앞뒤 맥락을 확인했나?", english: "Requested target, fields, source locator and adjacent context" },
  time: { title: "시점과 자료의 적용 가능성", question: "요청한 시점에 이 자료를 쓸 수 있나?", english: "Requested time and source applicability" },
  comparison: { title: "비교 조건", question: "두 기록의 방법, 조건, 단위, 기간이 같은가?", english: "Both records' method, conditions, units and period" },
  conversion: { title: "환산 근거", question: "필요한 환산에 공식 근거와 유효성이 있나?", english: "Authority and validity of any required conversion" },
  observation: { title: "문제와 관찰 범위", question: "문제와 관찰한 범위가 분명한가?", english: "Problem and observed scope" },
  counterevidence: { title: "반대 근거", question: "설명을 가려낼 예측과 반대 근거를 확인했나?", english: "Discriminating predictions and counterevidence" },
  test: { title: "적용할 시험", question: "원인으로 올리기 전에 적용할 시험이 있나?", english: "Applicable test profile before causal promotion" },
};

export type CriterionText = { title: string | null; question: string | null; technical: string[] };

/** What a criteria row shows: a title and a question in Korean, with codes, ids and untranslated English kept for the folded technical part. */
export function criterionText(target: string, question: string): CriterionText {
  const known = builtIn[target];
  const technical: string[] = [];
  let title: string | null = null;
  let shownQuestion: string | null = null;
  if (known) {
    title = known.title;
    if (question === known.english) shownQuestion = known.question;
  } else if (HANGUL.test(target)) {
    title = target;
  } else if (target) {
    technical.push(target);
  }
  if (shownQuestion === null) {
    if (isReadable(question)) shownQuestion = question || null;
    else technical.push(question);
  }
  return { title, question: shownQuestion, technical };
}
