export const judgmentMessages = {
  "testOrder.note": {
    "ko": "순서는 점수가 아니라 규칙입니다. 결과에 따라 남는 원인 후보가 적은 시험부터, 같으면 위험이 낮은 것, 비용이 낮은 것, 되돌릴 수 있는 것, 만든 순서로 놓습니다. 원인을 아직 모른다는 칸은 어떤 결과에서도 남습니다. 그럴듯함은 순서에 쓰지 않습니다(근거 상태는 가설 카드에서 따로 봅니다). 한 시험을 하고 나면 그 뒤 순서는 결과에 따라 바뀝니다.",
    "en": "Order follows rules, not a score. Tests leaving the fewest possible causes come first, then lower risk, lower cost, reversibility, and creation order. Unknown predictions remain possible under every outcome. Plausibility is not used for ordering; evidence is shown separately on hypothesis cards. Subsequent order changes after a test result."
  },
  "testOrder.excluded": {
    "ko": "순서에서 뺀 행동(금지 수준)",
    "en": "Actions excluded from ordering (prohibited risk)"
  },
  "testOrder.uncountable": {
    "ko": "다른 가설이 이 시험에서 무엇을 낼지 적혀 있지 않아, 후보를 하나만 줄이는 시험으로 셉니다.",
    "en": "Predictions for other hypotheses are missing, so this is counted as reducing the candidates by only one."
  },
  "testOrder.noReduction": {
    "ko": "이 시험만으로는 원인 후보를 줄이지 못합니다.",
    "en": "This test alone cannot reduce the possible causes."
  },
  "judgment.this": {
    "ko": "이 가설과 맞음",
    "en": "Fits this hypothesis"
  },
  "judgment.alternative": {
    "ko": "다른 설명과 맞음",
    "en": "Fits an alternative"
  },
  "judgment.neither": {
    "ko": "둘 다 아님",
    "en": "Fits neither"
  },
  "judgment.undetermined": {
    "ko": "판단 불가",
    "en": "Undetermined"
  },
  "judgment.recorded": {
    "ko": "기록됨",
    "en": "Recorded"
  },
  "judgment.excludedOnce": {
    "ko": "배제(결과 1건)",
    "en": "Excluded (one recorded result)"
  },
  "judgment.excludedRepeated": {
    "ko": "배제(반복 확인)",
    "en": "Excluded (repeated results)"
  },
  "judgment.eliminationNote": {
    "ko": "기록된 시험 결과가 다른 설명과 맞았다는 표시입니다. 가설은 지워지지 않고, 결과를 다시 기록하면 달라집니다.",
    "en": "Recorded test results fit an alternative explanation. The hypothesis is retained, and this indication can change when results are recorded again."
  },
  "judgment.againstOnce": {
    "ko": "다른 설명과 맞음(1건)",
    "en": "Fits an alternative (one result)"
  },
  "judgment.againstRepeated": {
    "ko": "다른 설명과 맞음(반복)",
    "en": "Fits an alternative (repeated)"
  },
  "judgment.mixed": {
    "ko": "엇갈림",
    "en": "Mixed results"
  },
  "judgment.standingNote": {
    "ko": "사람이 기록한 시험 결과를 읽은 것이며, 봉인된 시험으로 얻은 검증 상태를 바꾸지 않습니다.",
    "en": "This summarizes test results recorded by people. It does not change the verification state established by sealed tests."
  },
  "judgment.againstNote": {
    "ko": "이 행동이 기대는 가설이 기록된 시험 결과에서 다른 설명과 맞았습니다. 이 안내는 승인과 실행을 막지 않습니다. 시험 결과를 다시 살펴보고 판단하세요.",
    "en": "Recorded results for a hypothesis supporting this action fit an alternative explanation. This notice does not block approval or execution. Review the test results before deciding."
  },
  "judgment.fixApplied": {
    "ko": "수정 반영됨(효과 미확인)",
    "en": "Fix applied (effect unverified)"
  },
  "judgment.fixHint": {
    "ko": "고친 내용을 반영했지만 새 결과로 효과를 확인하지는 않았습니다. 새 결과가 들어와 이 줄이 충족으로 바뀌면 규칙이 '효과 확인됨'을 계산합니다.",
    "en": "A fix was applied, but its effect has not been verified by a new result. When a new result makes this row meet the criterion, the rule marks the effect as confirmed."
  },
  "judgment.humanClosed": {
    "ko": "사람 확인 종결(시험 결과 없음)",
    "en": "Human-confirmed closure (no test result)"
  },
  "judgment.humanHint": {
    "ko": "시험 결과 없이 사람이 확인해 닫았습니다.",
    "en": "A person confirmed closure without a test result."
  },
  "judgment.waiver": {
    "ko": "편차·면제 승인(원 기준 미충족 유지)",
    "en": "Deviation or waiver recorded (original criterion remains unmet)"
  },
  "judgment.waiverHint": {
    "ko": "시스템 밖에서 내려진 편차·면제 승인을 기록합니다. 원래 기준은 미충족으로 남습니다.",
    "en": "Records a deviation or waiver authorized outside the system. The original criterion remains unmet."
  },
  "judgment.conditionChanged": {
    "ko": "운용 조건 변경",
    "en": "Operating condition changed"
  },
  "judgment.conditionHint": {
    "ko": "기준이 적용되는 운용 조건이 바뀐 것을 기록합니다. 어느 조건 범위인지 적어야 합니다.",
    "en": "Records a change to the operating conditions where the criterion applies. The condition scope must be specified."
  },
  "judgment.closureNote": {
    "ko": "사람이 시스템 밖에서 내린 처분을 적어 두는 기록입니다. THOTH가 면제나 승인을 내리지 않으며, 이 줄의 판정(규칙이 계산한 값)은 그대로입니다.",
    "en": "Records a human disposition made outside the system. THOTH does not grant waivers or approvals; the rule-computed assessment remains unchanged."
  },
  "judgment.effectConfirmed": {
    "ko": "효과 확인됨",
    "en": "Effect confirmed"
  },
  "judgment.unspecified": {
    "ko": "미기재",
    "en": "Not specified"
  },
  "judgment.closureRecorded": {
    "ko": "처분 기록됨",
    "en": "Disposition recorded"
  },
  "judgment.effectNotice": {
    "ko": "수정을 기록한 뒤 들어온 새 결과로 이 줄이 충족으로 바뀌었습니다(규칙이 계산한 값).",
    "en": "A new result received after the fix was recorded made this row meet the criterion, as computed by the rule."
  },
  "judgment.externalWaiver": {
    "ko": "시스템 밖에서 내려진 처분을 적어 둔 기록입니다. THOTH가 면제를 내린 것이 아닙니다.",
    "en": "This records a disposition made outside the system. THOTH did not grant the waiver."
  },
  "judgment.verdictChanged": {
    "ko": "이 기록 뒤에 판정이 바뀌었습니다. 기록은 그때의 판정에 대한 것입니다.",
    "en": "The assessment changed after this record. The record refers to the assessment at that time."
  }
} as const;
