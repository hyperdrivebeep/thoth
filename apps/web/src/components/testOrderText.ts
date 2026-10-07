import type { GroupId, Ranked } from "./testRanking";

/** Plain words for the order of tests and actions. No score and no probability is ever shown; only whole-number counts of candidates. */

export const ORDER_NOTE = "순서는 점수가 아니라 규칙입니다. 결과에 따라 남는 원인 후보가 적은 시험부터, 같으면 위험이 낮은 것, 비용이 낮은 것, 되돌릴 수 있는 것, 만든 순서로 놓습니다. 원인을 아직 모른다는 칸은 어떤 결과에서도 남습니다. 그럴듯함은 순서에 쓰지 않습니다(근거 상태는 가설 카드에서 따로 봅니다). 한 시험을 하고 나면 그 뒤 순서는 결과에 따라 바뀝니다.";

const TITLES: Record<GroupId, { test: string; action: string; hint: string }> = {
  READY: { test: "먼저 해 볼 시험", action: "먼저 볼 행동", hint: "결과가 어느 쪽이어도 남는 원인 후보가 가장 적은 시험부터 놓았습니다." },
  COST_UNKNOWN: { test: "비용을 아직 모르는 시험", action: "비용을 아직 모르는 행동", hint: "같은 규칙으로 놓았지만 비용 추정이 없어 따로 두었습니다. 비용 0으로 보지 않습니다." },
  NO_DECISION_CHANGE: { test: "결과가 달라도 할 일이 같은 시험", action: "결과가 달라도 할 일이 같은 행동", hint: "어떤 결과가 나와도 이어지는 행동이 같아서 아래로 내렸습니다." },
  NOT_EXECUTABLE: { test: "지금은 할 수 없는 시험", action: "지금은 할 수 없는 행동", hint: "지금은 실행할 수 없습니다." },
  UNCOUNTABLE: { test: "얼마나 가르는지 아직 셀 수 없는 시험", action: "순서대로 본 행동", hint: "다른 가설이 이 시험에서 무엇을 낼지 적혀 있지 않아 가르는 정도를 셀 수 없습니다. 위험, 비용, 되돌림 순으로만 놓았습니다." },
  RESULT_RECORDED: { test: "결과를 이미 기록한 시험", action: "결과를 이미 기록한 행동", hint: "이미 한 시험이라 다음에 할 시험으로 내세우지 않습니다." },
  HYPOTHESIS_ELIMINATED: { test: "배제된 가설의 시험", action: "배제된 가설의 행동", hint: "반복한 시험 결과로 배제된 가설의 시험입니다. 후보 수에서는 뺐고 기록은 남아 있습니다." },
};
export const groupTitle = (group: GroupId, kind: "TEST" | "ACTION") => TITLES[group][kind === "TEST" ? "test" : "action"];
export const groupHint = (group: GroupId) => TITLES[group].hint;
export const EXCLUDED_TITLE = "순서에서 뺀 행동(금지 수준)";
export const excludedTestsLine = (count: number) => "금지 수준이라 순서에서 뺀 시험이 " + count + "개 있습니다.";

/** What the test does to the candidates, in whole numbers only. */
export function reductionLine(entry: Ranked, candidates: number): string {
  if (!entry.counted) return "다른 가설이 이 시험에서 무엇을 낼지 적혀 있지 않아, 후보를 하나만 줄이는 시험으로 셉니다.";
  if (entry.worst >= candidates) return "이 시험만으로는 원인 후보를 줄이지 못합니다.";
  return "이 시험은 결과에 따라 원인 후보를 최대 " + entry.worst + "개로 줄입니다.";
}
