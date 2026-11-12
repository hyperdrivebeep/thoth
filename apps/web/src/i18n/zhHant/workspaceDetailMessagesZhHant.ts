import type { ColumnOf } from "../withColumns";
import type { workspaceDetailMessages } from "../workspaceDetailMessages";

export const workspaceDetailZhHant: ColumnOf<typeof workspaceDetailMessages> = {
  "workspace.authorizedRead": "在你的存取權限範圍內，仍可閱讀已儲存的研究。",
  "workspace.readOnlyHold":
    "在你的權限範圍內，已儲存的研究仍可使用。新的模型執行和同意變更已暫緩。",
  "workspace.resumeFailed": "無法繼續研究。",
  "workspace.restoreScope":
    "其他位址、埠、瀏覽器設定檔或工作區中的選擇和草稿不會自動合併。選擇專案後會儲存新的位置。",
  "workspace.existingRead": "如果專案已存在，你可以在存取權限範圍內開啟其已儲存的研究。",
  "workspace.preparing": "正在檢查就緒狀態…",
  "workspace.readinessFailed": "伺服器就緒檢查失敗",
  "workspace.readinessAdvice": "無法檢查伺服器就緒狀態。請檢查連線後重試。",
  "workspace.loadingSaved": "正在檢查此工作區中已儲存的對話…",
  "workspace.checkingProjects": "正在檢查可存取的專案…",
  "workspace.accessFailed": "無法驗證專案存取權限。已儲存的工作不會被新專案取代。",
  "workspace.savedUnavailable": "此工作區中已儲存的研究目前不可用。請檢查你的存取權限。",
  "workspace.taskUnavailable": "無法驗證所選對話。你的草稿會保留。",
  "workspace.draftNotSent": "已儲存的草稿不會自動提交。",
  "setup.researchIndependent": "已儲存的研究與此設定檔案相互獨立，在具備所需權限時仍可存取。",
  "model.unavailable": "已儲存的模型選擇目前不可用。",
  "model.inheritedUnchanged": "此對話沿用專案的模型設定；沒有單獨的選擇取代它們。",
  "model.keepSelection": "你現有的選擇會保留。如需更改，請在下面選擇模型。",
  "model.modelLabel": "模型",
  "model.effortLabel": "推理強度",
};
