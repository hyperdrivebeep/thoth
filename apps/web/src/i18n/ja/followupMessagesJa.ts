import type { ColumnOf } from "../withColumns";
import type { followupMessages } from "../followupMessages";

export const followupJa: ColumnOf<typeof followupMessages> = {
  "followup.complete": "完了",
  "followup.partialHold": "一部保留",
  "followup.reviewNeeded": "レビューが必要",
  "followup.noResult": "まだ結果がありません",
  "followup.failed": "実行に失敗しました",
  "followup.cancelled": "キャンセル済み",
  "followup.unverifiable": "検証できません",
  "followup.terminal": "調査の実行が終了しました",
  "followup.checkpoint": "回答は途中のチェックポイントで保存されました",
  "followup.requirements": "判定の条件が記録されました",
  "followup.coverage": "条件ごとにカバー状況を評価しました",
  "followup.notProduced": "まだ回答は作成されていません",
  "followup.legacyBasis": "この古い回答は、根拠の出どころを示していません",
  "followup.requirementsUnavailable": "判定の条件を読み込めませんでした",
  "followup.coverageUnavailable": "条件ごとの判定を読み込めませんでした",
  "followup.unassessed": "まだ評価されていない条件があります",
  "followup.noEvidence": "このレビュー要約に個別に結びついた根拠はありません。",
  "followup.noComparison": "比較する回答が確定していないため、判定の変化は表示されません。",
  "followup.noChange": "サーバーには判定の変化が記録されていません。",
  "followup.changesUnavailable": "現在の権限では判定の変化を確認できません。",
  "followup.partialChanges": "判定の変化の一部しか検証できませんでした。",
  "followup.hasChanges": "サーバーが判定の変化を記録しました。",
  "followup.summaryLabel": "回答レビューの要約",
  "followup.title": "回答レビュー",
  "followup.statusNotice": "サーバーが記録した進み具合と根拠のカバー状況です。",
  "followup.reviewSummary": "レビュー要約",
  "followup.noProgress": "進み具合の要約は記録されていません。",
  "followup.linkedEvidence": "この要約に結びついた根拠",
  "followup.openEvidence": "元の根拠を開く",
  "followup.changes": "判定の変化",
  "followup.unknownChangeReason": "保存された記録には、変更の理由が示されていません。",
  "followup.nextAction": "いま必要な対応",
  "followup.authority": "この行動には承認の判断が必要です。",
  "followup.checks": "完了したチェックと残りのチェック",
  "followup.recordedChecks": "記録されたチェック",
  "followup.noChecks": "チェックは記録されていません。",
  "followup.remaining": "残りのチェック",
  "followup.noneRemaining": "残りのチェックは記録されていません。",
  "followup.noneUnknown": "検証できない項目は記録されていません。",
  "followup.inaccessibleChecks": "現在の権限では回答のチェックを確認できません。",
  "followup.noAnswerChecks": "この回答に結びついたチェックはありません。",
  "followup.checkStatus": "回答チェックのカバー状況",
  "followup.criterion": "基準",
  "followup.criterionStatus": "基準の状態",
  "followup.assessmentCriterion": "判定基準",
  "followup.noExplanation": "これ以上の説明はありません",
  "hold.stateChanged":
    "クリアする前に実行状態が変わりました。状態を更新しましたので、確認してからもう一度お試しください。",
  "hold.cannotClear":
    "この状態はクリアできません。すでにクリアされているか、テスト結果が確定している可能性があります。状態を更新しました。",
  "hold.clearFailed": "この試行をクリアできませんでした。しばらくしてからもう一度お試しください。",
  "hold.reasonNeeded": "試行をクリアする理由を入力してください。",
  "hold.notRun": "テストは実行されずに保留されました。",
  "hold.notRunNotice":
    "今回はサンドボックステストが実行されませんでした。理由は技術的な詳細をご覧ください。",
  "hold.notRepeated": "テストは繰り返されませんでした。",
  "hold.notRepeatedNotice":
    "テストは以前に実行されましたが、結果が未解決のため、同じ作業の重複を避けて繰り返しませんでした。",
  "hold.cleared": "試行をクリアしました。同じ質問をもう一度送ると、新しい実行が始まります。",
  "hold.draftReady": "質問を入力欄に入れました。",
  "hold.choice":
    "記録した理由とともに前回の試行をクリアし、そのあと同じ質問を送ると、新しい実行を1回始められます。前回の実行記録と結果の記録は保持されます。",
  "hold.clearAndRerun": "新しい実行の前に試行をクリアする",
  "hold.notImmediate":
    " これはすぐには実行されません。クリアしたあと、ご自身で質問を送って開始してください。",
  "hold.clearNotice":
    "以前のテストの未解決の結果をクリアします。誰がいつ、なぜクリアしたかが記録され、前回の実行と結果はそのまま残ります。",
  "hold.reasonLabel": "クリアする理由",
  "hold.reasonRequired": "クリアする理由（必須）",
  "hold.reasonExample": "例：入力ファイルを意図的に変更したあとで再実行するため",
  "hold.close": "閉じる",
  "hold.clear": "試行をクリア",
  "restore.error.changed":
    "プレビューのあとで記録が変更されました。再読み込みして、提案された変更をもう一度確認してください。",
  "restore.error.source":
    "資料のバージョンまたはアクセス権が変わったため、これらの変更は適用できません。",
  "restore.error.access": "現在の権限では、このリクエストの確認や適用はできません。",
  "restore.error.unsupported": "この項目の復元は未対応か、適用の条件を満たしていません。",
  "restore.error.idempotency":
    "入力が既存のリクエストと異なります。元のリクエストを保持し、その結果を確認してください。",
  "restore.error.unconfirmed":
    "サーバーは完了を確認しませんでした。元のリクエストの結果を確認してください。",
  "restore.error.schema":
    "サーバーの応答の形式を検証できませんでした。もう一度適用せず、元のリクエストの結果を確認してください。",
  "restore.error.connection":
    "接続の切断または応答の遅延により、適用の結果が不明です。元のリクエストの結果を確認してください。",
};
