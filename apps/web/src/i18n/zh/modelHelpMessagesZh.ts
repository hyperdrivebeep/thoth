import type { ColumnOf } from "../withColumns";
import type { modelHelpMessages } from "../modelHelpMessages";

export const modelHelpZh: ColumnOf<typeof modelHelpMessages> = {
  "modelHelp.retry": "如果模型调用被中断，只有该次调用会自动重试，最多两次，并会消耗额外的 token。关闭后，可在被中断的结果卡片上手动继续。",
  "modelHelp.retryReadFailed": "无法读取自动重试设置。",
  "modelHelp.retryLabel": "自动重试被中断的模型调用",
  "modelHelp.changeFailed": "无法更改该设置。视图已刷新，请重试。",
  "modelHelp.hypothesis": "在生成假设时，收集每个假设下各项测试的预期结果和反驳条件。补全缺失的预期结果可能需要额外调用一次模型。较长的回答会消耗更多 token。模型输出始终只是未经确认的建议。未保存选择时默认开启；已保存为关闭的选择会保持关闭。",
  "modelHelp.hypothesisReadFailed": "无法读取假设生成设置。",
  "modelHelp.hypothesisLabel": "包含预期结果和反驳条件（高级）",
  "modelHelp.staleCatalog": "无法刷新模型列表。当前显示的是上次验证过的列表；你已保存的选择不变。",
};
