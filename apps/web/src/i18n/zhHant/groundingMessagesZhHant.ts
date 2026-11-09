import type { ColumnOf } from "../withColumns";
import type { groundingMessages } from "../groundingMessages";

export const groundingZhHant: ColumnOf<typeof groundingMessages> = {
  "grounding.observation": "轉述已記錄的觀察 · AI 建議",
  "grounding.inference": "由資料推斷 · AI 建議",
  "grounding.hypothesis": "僅基於假設的預測 · AI 建議",
  "grounding.unclassified": "此預測的產生方式未分類",
  "grounding.unassessed": "與證據的關係尚未審查",
  "grounding.omitted": "部分預測因格式或證據連結問題被省略。其餘結果仍可檢視。",
  "grounding.hold": "由於無法驗證來源連結，新結果未能發布。",
  "grounding.saved": "上次儲存的結果會保留。",
};
