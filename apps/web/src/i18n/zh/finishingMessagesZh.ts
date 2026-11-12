import type { ColumnOf } from "../withColumns";
import type { finishingMessages } from "../finishingMessages";

export const finishingZh: ColumnOf<typeof finishingMessages> = {
  "progress.tableOnly": "只引用表格中的数字",
  "progress.noGuess": "不要猜测",
  "progress.withLocator": "附上原文位置作答",
  "progress.explainMissing": "未验证时说明缺少什么",
  "progress.elapsedUnknown": "耗时未知",
  "progress.noFirstByte": "尚未收到首字节",
  "progress.pausing": "正在完成当前阶段后暂停。",
  "progress.paused": "研究已暂停。",
  "progress.externalUnknown": "尚未确认外部执行已终止。",
  "usage.none": "此结果没有使用量记录",
  "usage.lessMinute": "不到一分钟",
  "usage.timeUnknown": "时长未知",
  "trace.noRule": "没有评估规则",
  "trace.noValue": "无值",
  "trace.failedCheck": "在每个未满足的标准行上，检查测试是否有效，以及是否存在其他原因。",
  "trace.excelFix": "恢复原来的 ID，将 ID 字段设为文本格式，然后重新保存。",
  "trace.invalidGraph": "应用此项会造成链接不一致。",
  "trace.checkLinks": "请确认每条链接中的两个 ID 和类型都存在于追溯表中。",
  "labels.targetQuoted": "对象：",
  "labels.evidenceSuffix": " · 证据 ",
  "labels.aiReview": "（基于 AI 审查）",
  "labels.command": "请在服务器终端运行此命令：",
};
