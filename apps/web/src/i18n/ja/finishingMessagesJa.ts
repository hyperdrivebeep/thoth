import type { ColumnOf } from "../withColumns";
import type { finishingMessages } from "../finishingMessages";

export const finishingJa: ColumnOf<typeof finishingMessages> = {
  "progress.tableOnly": "表の番号だけを引用する",
  "progress.noGuess": "推測しない",
  "progress.withLocator": "元の資料の位置を添えて答える",
  "progress.explainMissing": "未検証のときは何が足りないかを説明する",
  "progress.elapsedUnknown": "経過時間は不明",
  "progress.noFirstByte": "最初のバイトはまだ届いていません",
  "progress.pausing": "現在の段階を終えてから一時停止します。",
  "progress.paused": "タスクは一時停止中です。",
  "progress.externalUnknown": "外部での実行の終了はまだ確認されていません。",
  "usage.none": "この結果の使用量は記録されていません",
  "usage.lessMinute": "1分未満",
  "usage.timeUnknown": "所要時間は不明",
  "trace.noRule": "判定ルールなし",
  "trace.noValue": "値なし",
  "trace.failedCheck": "基準を満たさなかった各行で、テストが有効だったか、ほかの原因がないかを確認してください。",
  "trace.excelFix": "元の ID に戻し、ID 欄を文字列形式にして、もう一度保存してください。",
  "trace.invalidGraph": "これを適用すると、リンクが矛盾してしまいます。",
  "trace.checkLinks": "各リンクの両側の ID と種類が追跡表にあるかを確認してください。",
  "labels.targetQuoted": "対象：",
  "labels.evidenceSuffix": " · 根拠 ",
  "labels.aiReview": "（AI のレビューに基づく）",
  "labels.command": "サーバーのターミナルで次のコマンドを実行してください：",
};
