import type { ColumnOf } from "../withColumns";
import type { judgmentMessages } from "../judgmentMessages";

export const judgmentZhHant: ColumnOf<typeof judgmentMessages> = {
  "testOrder.note":
    "排序依據規則而非分數。能把可能原因縮小到最少的測試排在最前，其次是風險較低、成本較低、可逆的測試，最後按建立順序。未知的預測在每種結果下都仍然可能。排序不使用合理性；證據單獨顯示在假設卡片上。測試結果出來後，排序可能隨之變化。",
  "testOrder.excluded": "已排除在排序之外的行動（禁止的風險）",
  "testOrder.uncountable": "缺少其他假設的預測，因此只算作把候選原因減少了一個。",
  "testOrder.noReduction": "僅憑這一項測試無法縮小可能的原因。",
  "judgment.this": "符合此假設",
  "judgment.alternative": "符合另一種解釋",
  "judgment.neither": "兩者都不符合",
  "judgment.undetermined": "尚未確定",
  "judgment.recorded": "已記錄",
  "judgment.excludedOnce": "已排除（一次記錄結果）",
  "judgment.excludedRepeated": "已排除（多次結果）",
  "judgment.eliminationNote":
    "已記錄的測試結果符合另一種解釋。該假設仍然保留；再次記錄結果後，這一提示可能會改變。",
  "judgment.againstOnce": "符合另一種解釋（一次結果）",
  "judgment.againstRepeated": "符合另一種解釋（多次）",
  "judgment.mixed": "結果不一致",
  "judgment.standingNote": "這裡彙總的是人工記錄的測試結果。它不會改變封存測試所確立的驗證狀態。",
  "judgment.againstNote":
    "支持此行動的假設的已記錄結果符合另一種解釋。此提示不會阻止批准或執行。請在決定之前審查測試結果。",
  "judgment.fixApplied": "已應用修復（效果未驗證）",
  "judgment.fixHint":
    "已應用修復，但其效果尚未被新的結果驗證。當新的結果使這一行滿足標準時，規則會將該效果標記為已確認。",
  "judgment.humanClosed": "人工確認結案（無測試結果）",
  "judgment.humanHint": "有人在沒有測試結果的情況下確認結案。",
  "judgment.waiver": "已記錄偏差或豁免（原標準仍未滿足）",
  "judgment.waiverHint": "記錄在系統之外授權的偏差或豁免。原標準仍未滿足。",
  "judgment.conditionChanged": "執行條件已變化",
  "judgment.conditionHint": "記錄該標準所適用的執行條件發生了變化。必須指明條件的範圍。",
  "judgment.closureNote":
    "記錄在系統之外由人做出的處置。THOTH 不授予豁免或批准；由規則計算的評估保持不變。",
  "judgment.effectConfirmed": "效果已確認",
  "judgment.unspecified": "未指定",
  "judgment.closureRecorded": "已記錄處置",
  "judgment.effectNotice": "記錄修復之後收到的新結果，使這一行按規則計算滿足了標準。",
  "judgment.externalWaiver": "這裡記錄的是在系統之外做出的處置。THOTH 沒有授予豁免。",
  "judgment.verdictChanged": "此記錄之後評估發生了變化。該記錄指的是當時的評估。",
};
