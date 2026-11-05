import type { ZhOf } from "../withZh";
import type { groundingMessages } from "../groundingMessages";

export const groundingZh: ZhOf<typeof groundingMessages> = {
  "grounding.observation": "转述已记录的观察 · AI 建议",
  "grounding.inference": "由数据推断 · AI 建议",
  "grounding.hypothesis": "仅基于假设的预测 · AI 建议",
  "grounding.unclassified": "此预测的产生方式未分类",
  "grounding.unassessed": "与证据的关系尚未审查",
  "grounding.omitted": "部分预测因格式或证据链接问题被省略。其余结果仍可查看。",
  "grounding.hold": "由于无法验证来源链接，新结果未能发布。",
  "grounding.saved": "上次保存的结果会保留。",
};
