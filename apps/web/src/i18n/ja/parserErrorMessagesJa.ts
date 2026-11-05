import type { ColumnOf } from "../withColumns";
import type { parserErrorMessages } from "../parserErrorMessages";

export const parserErrorJa: ColumnOf<typeof parserErrorMessages> = {
  "parserError.AMBIGUOUS_PARSER_SELECTION": "THOTH はこのファイルの読み方を判断できませんでした。ファイルの種類と拡張子が一致しているか確認してください。",
  "parserError.PARSER_CAPABILITY_UNAVAILABLE": "このファイルに必要なリーダーが利用できません。インストールガイドのオプション機能を確認してください。",
  "parserError.PARSER_DEADLINE": "ファイルの読み込みに許容時間を超えました。小さなファイルに分けて、もう一度アップロードしてください。",
  "parserError.BYTE_HASH_MISMATCH": "読み込み中にファイルが変更されました。もう一度アップロードしてください。",
  "parserError.CORRUPT_DOCUMENT": "ファイルが壊れているか、想定した形式ではありません。別のプログラムで開いて保存し直してから、アップロードしてください。",
  "parserError.DIRTY_GIT_TARGET": "Git リポジトリにコミットされていない変更があります。コミットしてから、もう一度接続してください。",
  "parserError.ENCRYPTED_DOCUMENT": "ファイルはパスワードで保護されています。パスワードを外したコピーをアップロードしてください。",
  "parserError.EXTERNAL_LINK_IGNORED": "ファイル内のほかの場所へのリンクはたどっていません。必要な場合は、リンク先の内容を別に接続してください。",
  "parserError.FORMULA_NOT_RECALCULATED": "スプレッドシートの数式は再計算されず、保存されている値が読み込まれました。",
  "parserError.HIDDEN_CONTENT": "ファイルに非表示の内容があり、読み込まれませんでした。",
  "parserError.MEDIA_TYPE_MISMATCH": "拡張子が中身と一致しません。正しい拡張子で保存し直して、もう一度アップロードしてください。",
  "parserError.OCR_REQUIRED": "文字が画像になっているため読み込めませんでした。文字を選択できる形式のものをアップロードしてください。",
  "parserError.OVERSIZED_ARCHIVE": "アーカイブの内容が多すぎて読み込めません。必要なファイルだけをアップロードしてください。",
  "parserError.PARTIAL_EXTRACTION": "一部しか読み込めませんでした。ログインが必要なページやスクリプトで描画されるページは、保存したコピー（HTML または PDF）をアップロードしてください。",
  "parserError.TEXT_ENCODING_UNCONFIRMED": "文字エンコーディングを確認できませんでした。ファイルを UTF-8 で保存し直して、もう一度アップロードしてください。",
  "parserError.UNSUPPORTED_ELEMENT": "ファイルに未対応の要素があるため、その部分は読み込まれませんでした。",
  "parserError.UNSUPPORTED_MEDIA_TYPE": "このファイルの種類には対応していません。上に記載されている対応形式を確認してください。",
  "parserError.UNTRUSTED_LOG_LINE": "信頼できないログの行は読み込まれませんでした。",
  "parserError.ZIP_SLIP": "アーカイブに安全でないパスが含まれているため、拒否されました。ファイルを確認してください。",
  "parserError.unknown": "ファイルを読み込めませんでした。報告するときは、下の技術的な詳細を添えてください。",
  "sources.technicalDetails": "技術的な詳細",
};
