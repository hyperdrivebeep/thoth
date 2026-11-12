import type { ColumnOf } from "../withColumns";
import type { modelHelpMessages } from "../modelHelpMessages";

export const modelHelpZhHant: ColumnOf<typeof modelHelpMessages> = {
  "modelHelp.retry":
    "如果模型呼叫被中斷，只有該次呼叫會自動重試，最多兩次，並會消耗額外的 token。關閉後，可在被中斷的結果卡片上手動繼續。",
  "modelHelp.retryReadFailed": "無法讀取自動重試設定。",
  "modelHelp.retryLabel": "自動重試被中斷的模型呼叫",
  "modelHelp.changeFailed": "無法更改該設定。檢視已重新整理，請重試。",
  "modelHelp.hypothesis":
    "在生成假設時，收集每個假設下各項測試的預期結果和反駁條件。補全缺失的預期結果可能需要額外呼叫一次模型。較長的回答會消耗更多 token。模型輸出始終只是未經確認的建議。未儲存選擇時預設開啟；已儲存為關閉的選擇會保持關閉。",
  "modelHelp.hypothesisReadFailed": "無法讀取假設生成設定。",
  "modelHelp.hypothesisLabel": "包含預期結果和反駁條件（進階）",
  "modelHelp.staleCatalog":
    "無法重新整理模型列表。目前顯示的是上次驗證過的列表；你已儲存的選擇不變。",
};
