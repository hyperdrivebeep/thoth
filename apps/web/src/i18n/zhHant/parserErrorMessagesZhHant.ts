import type { ColumnOf } from "../withColumns";
import type { parserErrorMessages } from "../parserErrorMessages";

export const parserErrorZhHant: ColumnOf<typeof parserErrorMessages> = {
  "parserError.AMBIGUOUS_PARSER_SELECTION":
    "THOTH 無法確定如何讀取此檔案。請檢查檔案類型和副檔名是否一致。",
  "parserError.PARSER_CAPABILITY_UNAVAILABLE":
    "讀取此檔案所需的讀取器不可用。請檢視安裝指南中的可選元件。",
  "parserError.PARSER_DEADLINE":
    "讀取檔案的時間超過了允許的時長。請將其拆分為較小的檔案後重新上傳。",
  "parserError.BYTE_HASH_MISMATCH": "檔案在讀取過程中發生了變化。請重新上傳。",
  "parserError.CORRUPT_DOCUMENT":
    "檔案已損壞或格式不符合預期。請用其他程式開啟，重新儲存後再上傳。",
  "parserError.DIRTY_GIT_TARGET": "Git 儲存庫中有未提交的更改。請提交後重新連線。",
  "parserError.ENCRYPTED_DOCUMENT": "檔案受密碼保護。請上傳去除密碼的副本。",
  "parserError.EXTERNAL_LINK_IGNORED":
    "檔案中指向其他位置的連結沒有被跟進。如需這些內容，請單獨連線。",
  "parserError.FORMULA_NOT_RECALCULATED": "電子表格公式沒有重新計算，讀取的是已儲存的值。",
  "parserError.HIDDEN_CONTENT": "檔案含有隱藏內容，未被讀取。",
  "parserError.MEDIA_TYPE_MISMATCH": "副檔名與實際內容不符。請使用正確的副檔名儲存後重新上傳。",
  "parserError.OCR_REQUIRED": "文字只是圖片，因此無法讀取。請上傳可選中文字的版本。",
  "parserError.OVERSIZED_ARCHIVE": "壓縮包內容過多，無法讀取。請只上傳你需要的檔案。",
  "parserError.PARTIAL_EXTRACTION":
    "唯讀取了一部分。對於需要登入或由指令碼繪製的頁面，請上傳儲存的副本（HTML 或 PDF）。",
  "parserError.TEXT_ENCODING_UNCONFIRMED": "無法確認文字編碼。請將檔案另存為 UTF-8 後重新上傳。",
  "parserError.UNSUPPORTED_ELEMENT": "檔案中含有不受支援的元素，該部分未被讀取。",
  "parserError.UNSUPPORTED_MEDIA_TYPE": "不支援此檔案類型。請檢視上面列出的受支援類型。",
  "parserError.UNTRUSTED_LOG_LINE": "無法信任的日誌行沒有被讀取。",
  "parserError.ZIP_SLIP": "壓縮包中含有不安全的路徑，因此被拒絕。請檢查該檔案。",
  "parserError.unknown": "無法讀取該檔案。報告問題時請附上下面的技術詳情。",
  "sources.technicalDetails": "技術詳情",
};
