import type { ColumnOf } from "../withColumns";
import type { oauthMessages } from "../oauthMessages";

export const oauthJa: ColumnOf<typeof oauthMessages> = {
  "oauth.idle": "このログインの試行は、もう利用できません。アカウントの状態を更新してください。",
  "oauth.catalogLoading": "ログインが確認されました。モデル一覧を読み込んでいます。",
  "oauth.browser": "ブラウザでログインを完了してください。完了は自動的に確認されます。",
  "oauth.connected": "ログインしました。実際のモデルの応答は、最初の調査の実行時に検証されます。",
  "oauth.denied": "ログインが拒否されました。新しいログインは、選んだときにだけ始まります。",
  "oauth.expired": "このログインの試行は期限切れになりました。",
  "oauth.cancelled":
    "このログインの試行はキャンセルされました。既存の認証情報と API キーは保持されます。",
  "oauth.failed": "ログインに失敗しました。アカウントの状態を確認してください。",
  "oauth.installed": "ツールがインストールされました。ログインを開始できます。",
  "oauth.invalidStart":
    "ログインの試行 ID と状態を検証できませんでした。ログインは自動では再開されません。",
  "oauth.startUnknown": "ログイン開始の結果が不明です。新しい試行は、選んだときにだけ始まります。",
  "oauth.statusUnknown":
    "このログインの試行の状態を検証できませんでした。自動ポーリングは停止しました。",
  "oauth.noCatalog":
    "ログインには成功しましたが、モデル一覧を読み込めませんでした。接続を確認して、もう一度お試しください。",
  "oauth.refreshUnknown":
    "ログインの試行は終了しましたが、アカウントとモデルの情報を更新できませんでした。接続を手動で確認してください。",
  "oauth.pollFailed":
    "状態のポーリングに失敗して停止しました。新しいログインは開始されていません。",
  "oauth.cancelUnknown":
    "キャンセルを確認できませんでした。同じ試行の状態を手動で確認してください。",
  "oauth.cancelNotRetried": "キャンセルを確認できませんでした。自動では再試行されません。",
  "oauth.manualUnknown":
    "手動で入力した応答が受理されたかを確認できませんでした。入力内容は画面から消去されました。",
  "oauth.manualFailed":
    "手動で入力した応答の結果を検証できませんでした。入力内容は画面から消去されました。",
  "oauth.unsupported":
    "このコンピューターではアカウントによるログインを開始できません。API キーで接続してください。",
  "oauth.workspaceNeeded": "ワークスペースが検証されると、ログインを開始できます。",
  "oauth.openPage": "ログインページを開く",
  "oauth.checkTime": "状態確認の残り時間",
  "oauth.validTime": "有効期限までの時間",
  "oauth.checkManually": "状態を手動で確認",
  "oauth.cancel": "このログインをキャンセル",
  "oauth.manualInput": "コードまたはリダイレクト URL を手動で入力",
  "oauth.claudeResponse": "Claude の認証の応答",
  "oauth.submit": "この試行に送信",
  "oauth.technical": "技術的な詳細",
  "oauth.canAttempt": "試行可能",
  "oauth.notReady": "準備未完了",
  "oauth.verified": "検証済み",
  "oauth.unverified": "未検証",
  "xai.pending": "xAI の承認を待っています。ログインを開始しただけでは、接続は成立しません。",
  "xai.slowDown": "xAI の承認を待っています。状態の確認は間隔を空けて行われます。",
  "xai.connected": "xAI のログインが確認されました。使いたいモデルを選んでください。",
  "xai.denied": "xAI のログインが拒否されました。新しいコードを要求して、もう一度お試しください。",
  "xai.expired":
    "デバイスコードの期限が切れました。新しいコードを要求して、もう一度お試しください。",
  "xai.cancelled":
    "このログインのリクエストはキャンセルされました。既存の API キーでの接続は保持されます。",
  "xai.failed": "xAI のログインに失敗しました。接続を確認してから、もう一度お試しください。",
  "xai.requestUnknown":
    "現在のログインのリクエストを検証できませんでした。アカウントの状態を更新してください。",
  "xai.invalidStart":
    "xAI のログインの応答を検証できませんでした。新しいログインは自動では開始されません。",
  "xai.startUnknown":
    "xAI のログイン開始の結果が不明です。アカウントを確認してから、もう一度お試しください。",
  "xai.statusUnknown":
    "現在のログインのリクエストの状態が不明です。新しいログインは自動では開始されません。",
  "xai.refreshFailed":
    "xAI のログインは確認されましたが、アカウントとモデルの情報を更新できませんでした。接続を手動で確認してください。",
  "xai.pollFailed":
    "ログイン状態の確認に失敗して停止しました。新しいログインは開始されていません。",
  "xai.cancelUnknown":
    "キャンセルを確認できませんでした。同じログイン ID の状態を手動で確認してください。",
  "xai.unsupported":
    "このコンピューターでは xAI のログインを開始できません。API キーで接続してください。",
  "xai.loggedIn": "xAI にログイン済み",
  "xai.restart": "xAI のログインをやり直す",
  "xai.login": "xAI にログイン",
  "xai.workspaceNeeded": "ワークスペースが検証されると、xAI のログインを開始できます。",
  "xai.openApproval": "xAI の承認ページを開く",
  "web.execution.ready": "実行：準備完了",
  "web.execution.off": "実行：オフ",
  "web.execution.policy": "実行：権限の不一致",
  "web.execution.grant": "実行：付与の不一致",
  "web.execution.unavailable": "実行：利用不可",
  "web.execution.consent": "実行：ワークスペースの同意がありません",
  "web.execution.blocked": "実行：制限あり",
};
