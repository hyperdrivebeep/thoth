import type { ColumnOf } from "../withColumns";
import type { workspaceDetailMessages } from "../workspaceDetailMessages";

export const workspaceDetailJa: ColumnOf<typeof workspaceDetailMessages> = {
  "workspace.questionEvidence": "質問から根拠へ、根拠から次の行動へ。",
  "workspace.continueHeading": "調査を続けるか、新しい問題を始めてください。",
  "workspace.authorizedRead": "アクセス権限の範囲内で、保存済みの調査を引き続き読めます。",
  "workspace.readOnlyHold": "権限の範囲内で、保存済みの調査は引き続き利用できます。新しいモデルの実行と同意の変更は保留されています。",
  "workspace.resumeFailed": "調査を再開できませんでした。",
  "workspace.restoreScope": "ほかのアドレス、ポート、ブラウザプロファイル、ワークスペースの選択や下書きは自動では統合されません。プロジェクトを選ぶと、新しい場所が保存されます。",
  "workspace.existingRead": "プロジェクトがすでにある場合は、アクセス権限の範囲内で保存済みの調査を開けます。",
  "workspace.preparing": "準備状況を確認しています…",
  "workspace.readinessFailed": "サーバーの準備状況の確認に失敗しました",
  "workspace.readinessAdvice": "サーバーの準備状況を確認できませんでした。接続を確認して、もう一度お試しください。",
  "workspace.loadingSaved": "このワークスペースの保存済みタスクを確認しています…",
  "workspace.checkingProjects": "アクセスできるプロジェクトを確認しています…",
  "workspace.accessFailed": "プロジェクトへのアクセスを検証できませんでした。保存済みの作業が新しいプロジェクトに置き換わることはありません。",
  "workspace.savedUnavailable": "このワークスペースの保存済みの調査は、現在利用できません。アクセス権限を確認してください。",
  "workspace.taskUnavailable": "選択したタスクを検証できませんでした。下書きは保持されています。",
  "workspace.draftNotSent": "保存済みの下書きは自動では送信されません。",
  "setup.researchIndependent": "保存済みの調査はこの設定ファイルとは別で、必要な権限があれば引き続きアクセスできます。",
  "model.unavailable": "保存済みのモデルの選択は、現在利用できません。",
  "model.inheritedUnchanged": "このタスクはプロジェクトのモデル設定を引き継いでおり、個別の選択で置き換えられてはいません。",
  "model.keepSelection": "現在の選択は保持されます。変更するには、下でモデルを選んでください。",
  "model.modelLabel": "モデル",
  "model.effortLabel": "推論の強度",
};
