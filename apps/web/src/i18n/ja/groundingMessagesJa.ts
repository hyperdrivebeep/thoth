import type { ColumnOf } from "../withColumns";
import type { groundingMessages } from "../groundingMessages";

export const groundingJa: ColumnOf<typeof groundingMessages> = {
  "grounding.observation": "記録された観察を言い換えたもの · AI の提案",
  "grounding.inference": "データから推論したもの · AI の提案",
  "grounding.hypothesis": "仮説のみに基づく予測 · AI の提案",
  "grounding.unclassified": "この予測の作り方は分類されていません",
  "grounding.unassessed": "根拠との関係はまだ確認されていません",
  "grounding.omitted":
    "形式または根拠リンクの問題により、一部の予測を除外しました。残りの結果は確認できます。",
  "grounding.hold": "資料へのリンクを検証できなかったため、新しい結果を公開できませんでした。",
  "grounding.saved": "最後に保存された結果は保持されます。",
};
