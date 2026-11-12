import type { ColumnOf } from "../withColumns";
import type { modelHelpMessages } from "../modelHelpMessages";

export const modelHelpJa: ColumnOf<typeof modelHelpMessages> = {
  "modelHelp.retry":
    "モデル呼び出しが中断された場合、その呼び出しだけが最大2回まで自動で再試行され、追加のトークンを使います。オフにすると、中断された結果カードから手動で再開できます。",
  "modelHelp.retryReadFailed": "自動再試行の設定を読み込めませんでした。",
  "modelHelp.retryLabel": "中断されたモデル呼び出しを自動で再試行する",
  "modelHelp.changeFailed":
    "設定を変更できませんでした。表示を更新しましたので、もう一度お試しください。",
  "modelHelp.hypothesis":
    "仮説の生成中に、各仮説に対する各テストの期待結果と反証条件を集めます。不足している期待結果を補うために、モデル呼び出しが1回増えることがあります。回答が長くなると、トークンの使用量が増えます。モデルの出力は、確認されていない提案のままです。選択が保存されていない場合は既定でオンです。オフで保存した選択はオフのままです。",
  "modelHelp.hypothesisReadFailed": "仮説生成の設定を読み込めませんでした。",
  "modelHelp.hypothesisLabel": "期待結果と反証条件を含める（上級者向け）",
  "modelHelp.staleCatalog":
    "モデルの一覧を更新できませんでした。最後に検証した一覧を表示しており、保存済みの選択は変わりません。",
};
