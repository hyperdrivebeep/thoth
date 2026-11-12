import type { ColumnOf } from "../withColumns";
import type { judgmentMessages } from "../judgmentMessages";

export const judgmentZh: ColumnOf<typeof judgmentMessages> = {
  "testOrder.note":
    "排序依据规则而非分数。能把可能原因缩小到最少的测试排在最前，其次是风险较低、成本较低、可逆的测试，最后按创建顺序。未知的预测在每种结果下都仍然可能。排序不使用合理性；证据单独显示在假设卡片上。测试结果出来后，排序可能随之变化。",
  "testOrder.excluded": "已排除在排序之外的行动（禁止的风险）",
  "testOrder.uncountable": "缺少其他假设的预测，因此只算作把候选原因减少了一个。",
  "testOrder.noReduction": "仅凭这一项测试无法缩小可能的原因。",
  "judgment.this": "符合此假设",
  "judgment.alternative": "符合另一种解释",
  "judgment.neither": "两者都不符合",
  "judgment.undetermined": "尚未确定",
  "judgment.recorded": "已记录",
  "judgment.excludedOnce": "已排除（一次记录结果）",
  "judgment.excludedRepeated": "已排除（多次结果）",
  "judgment.eliminationNote":
    "已记录的测试结果符合另一种解释。该假设仍然保留；再次记录结果后，这一提示可能会改变。",
  "judgment.againstOnce": "符合另一种解释（一次结果）",
  "judgment.againstRepeated": "符合另一种解释（多次）",
  "judgment.mixed": "结果不一致",
  "judgment.standingNote": "这里汇总的是人工记录的测试结果。它不会改变封存测试所确立的验证状态。",
  "judgment.againstNote":
    "支持此行动的假设的已记录结果符合另一种解释。此提示不会阻止批准或执行。请在决定之前审查测试结果。",
  "judgment.fixApplied": "已应用修复（效果未验证）",
  "judgment.fixHint":
    "已应用修复，但其效果尚未被新的结果验证。当新的结果使这一行满足标准时，规则会将该效果标记为已确认。",
  "judgment.humanClosed": "人工确认结案（无测试结果）",
  "judgment.humanHint": "有人在没有测试结果的情况下确认结案。",
  "judgment.waiver": "已记录偏差或豁免（原标准仍未满足）",
  "judgment.waiverHint": "记录在系统之外授权的偏差或豁免。原标准仍未满足。",
  "judgment.conditionChanged": "运行条件已变化",
  "judgment.conditionHint": "记录该标准所适用的运行条件发生了变化。必须指明条件的范围。",
  "judgment.closureNote":
    "记录在系统之外由人做出的处置。THOTH 不授予豁免或批准；由规则计算的评估保持不变。",
  "judgment.effectConfirmed": "效果已确认",
  "judgment.unspecified": "未指定",
  "judgment.closureRecorded": "已记录处置",
  "judgment.effectNotice": "记录修复之后收到的新结果，使这一行按规则计算满足了标准。",
  "judgment.externalWaiver": "这里记录的是在系统之外做出的处置。THOTH 没有授予豁免。",
  "judgment.verdictChanged": "此记录之后评估发生了变化。该记录指的是当时的评估。",
};
