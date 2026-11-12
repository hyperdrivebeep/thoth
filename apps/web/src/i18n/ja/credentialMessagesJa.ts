import type { ColumnOf } from "../withColumns";
import type { credentialMessages } from "../credentialMessages";

export const credentialJa: ColumnOf<typeof credentialMessages> = {
  "connection.timeout":
    "THOTH サーバーが時間内に応答しませんでした。しばらくしてからもう一度お試しください。",
  "connection.offline": "THOTH サーバーに接続できませんでした。起動しているか確認してください。",
  "connection.intro.openai":
    "ChatGPT アカウントでサインインするか、OpenAI API キーで接続してください。",
  "connection.intro.anthropic":
    "Claude アカウントでサインインするか、Anthropic API キーで接続してください。",
  "connection.intro.xai": "xAI アカウントでサインインするか、xAI API キーで接続してください。",
  "connection.intro.key": "API キーで接続してください。",
  "connection.checking": "確認中",
  "connection.disconnected": "未接続",
  "connection.connected": "接続済み",
  "connection.toolNeeded": "ツールのインストールが必要",
  "connection.loggingIn": "ログイン中",
  "connection.browserLogin": "ブラウザでの操作が終わると、ログインは自動的に確認されます。",
  "connection.modelsNeeded": "モデル一覧が必要",
  "connection.loadAfterLogin": "ログインが完了しました。続けるにはモデル一覧を読み込んでください。",
  "connection.checkFailed": "確認に失敗",
  "connection.refreshAdvice": "接続状態を検証できませんでした。更新して、もう一度お試しください。",
  "credential.noRoute": "接続経路なし",
  "credential.noCodex": "Codex の実行ファイルがありません",
  "credential.noVersion": "対応バージョンがありません",
  "credential.reviewUpdate": "更新の確認が必要",
  "credential.loginRequired": "ログインが必要",
  "credential.loginPending": "ログインの進行中",
  "credential.noCatalog": "モデル一覧を利用できません",
  "credential.canTry": "実行可能 · 成功は未検証",
  "credential.unverified": "実行は未検証",
  "credential.checkFailed": "接続の確認に失敗しました",
  "credential.keyReady": "キー登録済み · 接続可能",
  "credential.loginReady": "ログイン確認済み · 接続可能",
  "credential.none": "まだありません",
  "credential.routeNeedsCheck": "接続経路の確認が必要",
  "credential.loginAndKey": "ログイン確認済み · キー登録済み",
  "credential.loginConfirmed": "ログイン確認済み",
  "credential.keyRegistered": "キー登録済み",
  "credential.statusNeeded": "接続状態の確認が必要",
  "credential.thirdPartyUnverified":
    "第三者の利用許可と、モデルの実行成功は、まだ検証されていません。",
  "credential.firstRunVerify": "実際のモデルの実行は、最初の調査の実行時に確認されます。",
  "credential.localEligible":
    "ローカルでの実行資格は確認されましたが、プロバイダー側での実行成功は未検証のままです。",
  "credential.notEligible": "新しいモデル実行の資格は、まだ確認されていません。",
  "credential.modelUnverified": "モデルの実行成功は、まだ検証されていません。",
  "credential.providerUnverified": "プロバイダーへの実際の呼び出しは、まだ検証されていません。",
  "credential.isolatedCodex":
    "THOTH は別のプロファイルを使い、既存の Codex デスクトップ版の認証情報は取り込みません。",
  "credential.isolated": "THOTH は専用の別プロファイルを使います。",
  "credential.xaiIsolated":
    "xAI のログインは、この THOTH ワークスペース専用の認証を使います。API キーでの接続は保持されます。",
  "credential.experimental": "Codex のモデル接続は実験的なものです。",
  "credential.claudeCodeProfile":
    "Claude のログインは、公式の Claude Code が THOTH 専用のプロファイルで処理します。",
  "credential.claudeOauthChoice": "Claude OAuth は別の経路で、明示的に選んだときだけ開始されます。",
  "credential.loginThenRefresh": "ログインを完了してから、接続状態を更新してください。",
  "credential.startNotComplete":
    "ログインを完了してから、接続状態を更新してください。ログインを開始しただけでは、接続は確認されません。",
  "credential.checkInstall":
    "実行ファイルと対応バージョンを確認してください。THOTH が自動でインストールすることも、既存のデスクトップ版の認証情報を変更することもありません。",
  "credential.catalogUnverified": "ログインとは別に、モデル一覧を検証することはできませんでした。",
  "credential.routeUnavailable": "利用できる THOTH の接続経路を検証できませんでした。",
  "credential.noSelectableRoute": "現在、選択できるモデル経路はありません。",
  "credential.claudeCodeHint":
    "Claude のログインは公式の Claude Code を使い、選んだときだけ開始されます。API キーでの接続は別です。",
  "credential.claudeOauthHint":
    "Claude Code の案内と、THOTH 専用の Claude OAuth は別です。OAuth のログインは、選んだときだけ開始されます。",
  "credential.xaiReady":
    "xAI のログインが確認されました。モデル一覧から xai-oauth 経路を選べます。",
  "credential.xaiNoRoute":
    "xAI のログインは確認されましたが、利用できるモデル経路はまだありません。もう一度ログインする前に、モデル一覧を確認してください。",
  "credential.xaiStart":
    "このワークスペース用に xAI のデバイスコードによるログインを開始するか、API キーを別に登録してください。",
  "credential.codexReady":
    "Codex のログインが確認されました。モデル一覧が表示されない場合は、もう一度ログインする前にモデル経路を確認してください。",
  "credential.codexStart":
    "THOTH 専用の Codex ログインを開始するか、API キーを登録してください。ログインを開始しただけでは、完了は確認されません。",
  "credential.keyOnly":
    "アカウントによるログインには対応していません。API キーを取得して登録してください。",
  "credential.methodsPending":
    "ログインへの対応をサーバーで確認すると、接続方法が表示されます。API キーは別に登録できます。",
  "credential.keySaved":
    "API キーは THOTH のローカルストアに登録されました。実際にモデルを使えるかは、使用時に確認されます。",
  "credential.sameWorkspace":
    "このサーバーが使うワークスペース向けの、THOTH 専用の接続方法を確認してください。",
  "credential.obtainKey":
    "アカウントによるログインには対応していません。プロバイダーのサイトで API キーを作成し、THOTH に登録してください。サイトを開くだけでは、接続は成立しません。",
  "credential.notConfirmed": "接続を確認できませんでした。接続状態を更新してください。",
};
