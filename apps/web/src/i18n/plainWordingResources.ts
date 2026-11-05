import { bilingualRecord } from "./bilingualRecord";
const reasonKo = {
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
const reasonEn: Record<keyof typeof reasonKo,string> = {
  "decision scope is explicit": "The decision scope is explicit.",
  "decision question is empty": "The decision question is empty.",
  "official evaluator-ready criterion exists": "An official criterion is ready for evaluation.",
  "criteria exist but official evaluator inputs are incomplete": "Criteria exist, but inputs for official evaluation are incomplete.",
  "no criterion candidate exists": "No criterion candidate exists.",
  "verified supporting evidence exists": "Verified supporting evidence exists.",
  "evidence is extracted but not support-verified": "Evidence was extracted, but support has not been verified.",
  "no cutoff-eligible evidence exists": "No evidence is eligible at the cutoff.",
  "comparison conditions are confirmed": "Comparison conditions are confirmed.",
  "comparison conditions conflict": "Comparison conditions conflict.",
  "comparison conditions have not been confirmed": "Comparison conditions have not been confirmed.",
  "counterevidence pass is recorded": "A counterevidence search is recorded.",
  "counterevidence has not been checked": "Counterevidence has not been checked.",
  "measurement implementation is verified": "The measurement implementation is verified.",
  "measurement implementation failed verification": "The measurement implementation failed verification.",
  "measurement implementation is not verified": "The measurement implementation is not verified.",
  "expert-confirmed semantics are bound to an official criterion": "Expert-confirmed meaning is bound to an official criterion.",
  "criterion semantics still require expert confirmation": "The criterion meaning still requires expert confirmation.",
  "no criterion semantics are available": "No criterion meaning is available."
};
const reasonZh: Record<keyof typeof reasonKo, string> = {
  "decision scope is explicit": "要判断什么是明确的。",
  "decision question is empty": "要判断什么的问题是空的。",
  "official evaluator-ready criterion exists": "正式标准已具备用于评判的条件。",
  "criteria exist but official evaluator inputs are incomplete": "标准已有，但正式评判所需的输入还不齐全。",
  "no criterion candidate exists": "没有可作为标准的候选。",
  "verified supporting evidence exists": "有经过确认的支持证据。",
  "evidence is extracted but not support-verified": "证据已提取，但尚未确认是否支持。",
  "no cutoff-eligible evidence exists": "在截止时间内没有可用的证据。",
  "comparison conditions are confirmed": "比较条件已确认。",
  "comparison conditions conflict": "比较条件相互不一致。",
  "comparison conditions have not been confirmed": "尚未确认比较条件是否相同。",
  "counterevidence pass is recorded": "已有寻找反面证据的记录。",
  "counterevidence has not been checked": "尚未寻找反面证据。",
  "measurement implementation is verified": "已确认测量方式是正确的。",
  "measurement implementation failed verification": "已核对测量方式，但结果不正确。",
  "measurement implementation is not verified": "尚未确认测量方式是否正确。",
  "expert-confirmed semantics are bound to an official criterion": "专家已确认标准的含义，并与正式标准关联。",
  "criterion semantics still require expert confirmation": "标准的确切含义还需要专家确认。",
  "no criterion semantics are available": "没有可用来了解标准含义的资料。",
};
const reasonJa: Record<keyof typeof reasonKo, string> = {
  "decision scope is explicit": "何を判断するのかが明確です。",
  "decision question is empty": "何を判断するかの質問が空です。",
  "official evaluator-ready criterion exists": "公式の基準が、判定に使える形で整っています。",
  "criteria exist but official evaluator inputs are incomplete": "基準はありますが、公式の判定に必要な入力がまだそろっていません。",
  "no criterion candidate exists": "基準にできそうな候補がありません。",
  "verified supporting evidence exists": "確認済みの裏づけとなる根拠があります。",
  "evidence is extracted but not support-verified": "根拠は抽出しましたが、裏づけになるかはまだ確認していません。",
  "no cutoff-eligible evidence exists": "基準時点の範囲内で使える根拠がありません。",
  "comparison conditions are confirmed": "比較の条件が確認されました。",
  "comparison conditions conflict": "比較の条件が食い違っています。",
  "comparison conditions have not been confirmed": "比較の条件が同じかどうか、まだ確認できていません。",
  "counterevidence pass is recorded": "反証の根拠を探した記録があります。",
  "counterevidence has not been checked": "反証の根拠は、まだ探していません。",
  "measurement implementation is verified": "測定の方法が正しいことが確認されました。",
  "measurement implementation failed verification": "測定の方法を確認しましたが、正しくありませんでした。",
  "measurement implementation is not verified": "測定の方法が正しいかどうか、まだ確認できていません。",
  "expert-confirmed semantics are bound to an official criterion": "基準の意味を専門家が確認し、公式の基準に結びつけました。",
  "criterion semantics still require expert confirmation": "基準が正確に何を意味するのか、専門家の確認がさらに必要です。",
  "no criterion semantics are available": "基準が何を意味するかを知るための資料がありません。",
};
export const plainReasons: Record<string,string> = bilingualRecord(reasonKo,reasonEn,reasonZh,reasonJa);

const criteriaKo = {
  answer: { title: "요청한 대상과 위치", question: "요청한 대상, 항목, 원문 위치와 앞뒤 맥락을 확인했나?", english: "Requested target, fields, source locator and adjacent context" },
  time: { title: "시점과 자료의 적용 가능성", question: "요청한 시점에 이 자료를 쓸 수 있나?", english: "Requested time and source applicability" },
  comparison: { title: "비교 조건", question: "두 기록의 방법, 조건, 단위, 기간이 같은가?", english: "Both records' method, conditions, units and period" },
  conversion: { title: "환산 근거", question: "필요한 환산에 공식 근거와 유효성이 있나?", english: "Authority and validity of any required conversion" },
  observation: { title: "문제와 관찰 범위", question: "문제와 관찰한 범위가 분명한가?", english: "Problem and observed scope" },
  counterevidence: { title: "반대 근거", question: "설명을 가려낼 예측과 반대 근거를 확인했나?", english: "Discriminating predictions and counterevidence" },
  test: { title: "적용할 시험", question: "원인으로 올리기 전에 적용할 시험이 있나?", english: "Applicable test profile before causal promotion" },
};
const criteriaEn: Record<keyof typeof criteriaKo,{title:string;question:string;english:string}> = {
  "answer": {
    "title": "Requested target and location",
    "question": "Were the requested target, fields, source location and surrounding context checked?",
    "english": "Requested target, fields, source locator and adjacent context"
  },
  "time": {
    "title": "Timing and source applicability",
    "question": "Can this source be used at the requested time?",
    "english": "Requested time and source applicability"
  },
  "comparison": {
    "title": "Comparison conditions",
    "question": "Do the methods, conditions, units and periods of both records match?",
    "english": "Both records' method, conditions, units and period"
  },
  "conversion": {
    "title": "Conversion basis",
    "question": "Does the required conversion have an authoritative and valid basis?",
    "english": "Authority and validity of any required conversion"
  },
  "observation": {
    "title": "Problem and observation scope",
    "question": "Are the problem and observed scope explicit?",
    "english": "Problem and observed scope"
  },
  "counterevidence": {
    "title": "Counterevidence",
    "question": "Were discriminating predictions and counterevidence checked?",
    "english": "Discriminating predictions and counterevidence"
  },
  "test": {
    "title": "Applicable tests",
    "question": "Is an applicable test available before causal promotion?",
    "english": "Applicable test profile before causal promotion"
  }
};
const criteriaZh: Record<keyof typeof criteriaKo, { title: string; question: string; english: string }> = {
  answer: { title: "所问的对象和位置", question: "是否已核对所问的对象、项目、原文位置及其前后背景？", english: "Requested target, fields, source locator and adjacent context" },
  time: { title: "时间与资料的适用性", question: "在所问的时间点，这份资料能用吗？", english: "Requested time and source applicability" },
  comparison: { title: "比较条件", question: "两条记录的方法、条件、单位和期间是否相同？", english: "Both records' method, conditions, units and period" },
  conversion: { title: "换算依据", question: "所需的换算是否有官方依据且有效？", english: "Authority and validity of any required conversion" },
  observation: { title: "问题与观察范围", question: "问题和观察到的范围是否明确？", english: "Problem and observed scope" },
  counterevidence: { title: "反面证据", question: "是否已核对能区分各种解释的预测和反面证据？", english: "Discriminating predictions and counterevidence" },
  test: { title: "要采用的测试", question: "在把某项当作原因之前，是否有可采用的测试？", english: "Applicable test profile before causal promotion" },
};
const criteriaJa: Record<keyof typeof criteriaKo, { title: string; question: string; english: string }> = {
  answer: { title: "求められた対象と位置", question: "求められた対象、項目、元の資料の位置と前後の文脈を確認しましたか？", english: "Requested target, fields, source locator and adjacent context" },
  time: { title: "時点と資料の適用可能性", question: "求められた時点で、この資料を使えますか？", english: "Requested time and source applicability" },
  comparison: { title: "比較の条件", question: "2つの記録の方法、条件、単位、期間は同じですか？", english: "Both records' method, conditions, units and period" },
  conversion: { title: "換算の根拠", question: "必要な換算に、公式の根拠と有効性がありますか？", english: "Authority and validity of any required conversion" },
  observation: { title: "問題と観察の範囲", question: "問題と、観察した範囲は明確ですか？", english: "Problem and observed scope" },
  counterevidence: { title: "反証の根拠", question: "説明を見分ける予測と、反証の根拠を確認しましたか？", english: "Discriminating predictions and counterevidence" },
  test: { title: "適用するテスト", question: "原因として挙げる前に、適用できるテストがありますか？", english: "Applicable test profile before causal promotion" },
};
export const builtinCriteria: Record<string,{title:string;question:string;english:string}> = bilingualRecord(criteriaKo,criteriaEn,criteriaZh,criteriaJa);
