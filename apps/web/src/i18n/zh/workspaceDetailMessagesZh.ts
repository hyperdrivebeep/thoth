import type { ColumnOf } from "../withColumns";
import type { workspaceDetailMessages } from "../workspaceDetailMessages";

export const workspaceDetailZh: ColumnOf<typeof workspaceDetailMessages> = {
  "workspace.questionEvidence": "从问题到证据，再从证据到下一步行动。",
  "workspace.continueHeading": "继续你的研究，或开始一个新问题。",
  "workspace.authorizedRead": "在你的访问权限范围内，仍可阅读已保存的研究。",
  "workspace.readOnlyHold": "在你的权限范围内，已保存的研究仍可使用。新的模型运行和同意变更已暂缓。",
  "workspace.resumeFailed": "无法继续研究。",
  "workspace.restoreScope": "其他地址、端口、浏览器配置文件或工作区中的选择和草稿不会自动合并。选择项目后会保存新的位置。",
  "workspace.existingRead": "如果项目已存在，你可以在访问权限范围内打开其已保存的研究。",
  "workspace.preparing": "正在检查就绪状态…",
  "workspace.readinessFailed": "服务器就绪检查失败",
  "workspace.readinessAdvice": "无法检查服务器就绪状态。请检查连接后重试。",
  "workspace.loadingSaved": "正在检查此工作区中已保存的任务…",
  "workspace.checkingProjects": "正在检查可访问的项目…",
  "workspace.accessFailed": "无法验证项目访问权限。已保存的工作不会被新项目取代。",
  "workspace.savedUnavailable": "此工作区中已保存的研究目前不可用。请检查你的访问权限。",
  "workspace.taskUnavailable": "无法验证所选任务。你的草稿会保留。",
  "workspace.draftNotSent": "已保存的草稿不会自动提交。",
  "setup.researchIndependent": "已保存的研究与此设置文件相互独立，在具备所需权限时仍可访问。",
  "model.unavailable": "已保存的模型选择目前不可用。",
  "model.inheritedUnchanged": " 此任务沿用项目的模型设置；没有单独的选择取代它们。",
  "model.keepSelection": "你现有的选择会保留。如需更改，请在下面选择模型。",
  "model.modelLabel": "模型",
  "model.effortLabel": "推理强度",
};
