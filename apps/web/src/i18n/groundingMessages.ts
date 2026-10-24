/** G2 r3 approved display copy; canonical state values are never translated or rendered raw. */
export const groundingMessages = {
  "grounding.observation": { ko: "자료의 관측을 옮김 · AI 제안", en: "Restates a recorded observation · AI proposal" },
  "grounding.inference": { ko: "자료에서 추론 · AI 제안", en: "Inferred from the data · AI proposal" },
  "grounding.hypothesis": { ko: "가설에 따른 예상 · AI 제안", en: "Hypothesis-only prediction · AI proposal" },
  "grounding.unclassified": { ko: "예측 방식 미분류", en: "How this prediction was made is not classified" },
  "grounding.unassessed": { ko: "근거와의 관계는 아직 검토하지 않음", en: "Relation to the evidence not yet reviewed" },
  "grounding.omitted": { ko: "일부 예측을 형식 또는 근거 연결 문제로 제외했습니다. 나머지 결과는 확인할 수 있습니다.", en: "Some predictions were omitted because of format or evidence-link problems. The remaining results are available." },
  "grounding.hold": { ko: "출처 연결을 확인하지 못해 새 결과를 표시하지 못했습니다.", en: "The new result could not be published because its source links could not be verified." },
  "grounding.saved": { ko: "마지막 저장 결과는 유지됩니다.", en: "The last saved result is preserved." },
} as const;
