import type { ColumnOf } from "../withColumns";
import type { finishingMessages } from "../finishingMessages";

export const finishingZhHant: ColumnOf<typeof finishingMessages> = {
  "progress.tableOnly": "只引用表格中的數字",
  "progress.noGuess": "不要猜測",
  "progress.withLocator": "附上原文位置作答",
  "progress.explainMissing": "未驗證時說明缺少什麼",
  "progress.elapsedUnknown": "耗時未知",
  "progress.noFirstByte": "尚未收到首位元組",
  "progress.pausing": "正在完成目前階段後暫停。",
  "progress.paused": "研究已暫停。",
  "progress.externalUnknown": "尚未確認外部執行已終止。",
  "usage.none": "此結果沒有使用量記錄",
  "usage.lessMinute": "不到一分鐘",
  "usage.timeUnknown": "時長未知",
  "trace.noRule": "沒有評估規則",
  "trace.noValue": "無值",
  "trace.failedCheck": "在每個未滿足的標準行上，檢查測試是否有效，以及是否存在其他原因。",
  "trace.excelFix": "還原原來的 ID，將 ID 欄位設為文字格式，然後重新儲存。",
  "trace.invalidGraph": "應用此項會造成連結不一致。",
  "trace.checkLinks": "請確認每條連結中的兩個 ID 和類型都存在於追溯表中。",
  "labels.targetQuoted": "物件：",
  "labels.evidenceSuffix": " · 證據 ",
  "labels.aiReview": "（基於 AI 審查）",
  "labels.command": "請在伺服器終端執行此命令：",
};
