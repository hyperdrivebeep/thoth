import { bilingualRecord } from "./bilingualRecord";
const korean = {
  READY: { test: "먼저 해 볼 시험", action: "먼저 볼 행동", hint: "결과가 어느 쪽이어도 남는 원인 후보가 가장 적은 시험부터 놓았습니다." },
  COST_UNKNOWN: { test: "비용을 아직 모르는 시험", action: "비용을 아직 모르는 행동", hint: "같은 규칙으로 놓았지만 비용 추정이 없어 따로 두었습니다. 비용 0으로 보지 않습니다." },
  NO_DECISION_CHANGE: { test: "결과가 달라도 할 일이 같은 시험", action: "결과가 달라도 할 일이 같은 행동", hint: "어떤 결과가 나와도 이어지는 행동이 같아서 아래로 내렸습니다." },
  NOT_EXECUTABLE: { test: "지금은 할 수 없는 시험", action: "지금은 할 수 없는 행동", hint: "지금은 실행할 수 없습니다." },
  UNCOUNTABLE: { test: "얼마나 가르는지 아직 셀 수 없는 시험", action: "순서대로 본 행동", hint: "다른 가설이 이 시험에서 무엇을 낼지 적혀 있지 않아 가르는 정도를 셀 수 없습니다. 위험, 비용, 되돌림 순으로만 놓았습니다." },
  RESULT_RECORDED: { test: "결과를 이미 기록한 시험", action: "결과를 이미 기록한 행동", hint: "이미 한 시험이라 다음에 할 시험으로 내세우지 않습니다." },
  HYPOTHESIS_ELIMINATED: { test: "배제된 가설의 시험", action: "배제된 가설의 행동", hint: "반복한 시험 결과로 배제된 가설의 시험입니다. 후보 수에서는 뺐고 기록은 남아 있습니다." },
};
const english: Record<keyof typeof korean, {test: string; action: string; hint: string}> = {
  "READY": {
    "test": "Tests to try first",
    "action": "Actions to review first",
    "hint": "Tests leaving the fewest possible causes under every outcome come first."
  },
  "COST_UNKNOWN": {
    "test": "Tests with unknown cost",
    "action": "Actions with unknown cost",
    "hint": "The same ordering rule applies, but these are separate because cost is unknown. Unknown cost is not zero."
  },
  "NO_DECISION_CHANGE": {
    "test": "Tests whose outcomes lead to the same action",
    "action": "Actions unchanged by the outcome",
    "hint": "These are lower because every outcome leads to the same next actions."
  },
  "NOT_EXECUTABLE": {
    "test": "Tests that cannot run now",
    "action": "Actions that cannot run now",
    "hint": "These cannot currently be executed."
  },
  "UNCOUNTABLE": {
    "test": "Tests whose separation cannot yet be counted",
    "action": "Actions in rule order",
    "hint": "Predictions for other hypotheses are missing, so separation cannot be counted. Only risk, cost, and reversibility determine the order."
  },
  "RESULT_RECORDED": {
    "test": "Tests with recorded results",
    "action": "Actions with recorded results",
    "hint": "These tests were already performed and are not offered as the next tests."
  },
  "HYPOTHESIS_ELIMINATED": {
    "test": "Tests for excluded hypotheses",
    "action": "Actions for excluded hypotheses",
    "hint": "Repeated recorded test results excluded these hypotheses from the candidate count. Their records are retained."
  }
};
export const testOrderTitles = bilingualRecord(korean, english);
