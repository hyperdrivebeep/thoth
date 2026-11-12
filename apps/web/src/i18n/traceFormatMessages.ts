import { eulreul, eunneun, iga } from "../api/koreanParticles";
export const traceFormats = {
  traceResultRemoved: {
    ko: (name: string) => `결과 삭제: ${name}`,
    en: (name: string) => `Result removed: ${name}`,
    "zh-Hans": (name: string) => `已移除结果：${name}`,
    "zh-Hant": (name: string) => `已移除結果：${name}`,
    ja: (name: string) => `結果を削除：${name}`,
  },
  traceResultAdded: {
    ko: (name: string, detail: string) => `결과 추가: ${name}${detail}`,
    en: (name: string, detail: string) => `Result added: ${name}${detail}`,
    "zh-Hans": (name: string, detail: string) => `已添加结果：${name}${detail}`,
    "zh-Hant": (name: string, detail: string) => `已新增結果：${name}${detail}`,
    ja: (name: string, detail: string) => `結果を追加：${name}${detail}`,
  },
  traceResultChanged: {
    ko: (name: string, field: string, before: string, after: string) =>
      `결과 ${name}의 ${field}: ${before} → ${after}`,
    en: (name: string, field: string, before: string, after: string) =>
      `Result ${name}, ${field}: ${before} → ${after}`,
    "zh-Hans": (name: string, field: string, before: string, after: string) =>
      `结果 ${name} 的${field}：${before} → ${after}`,
    "zh-Hant": (name: string, field: string, before: string, after: string) =>
      `結果 ${name} 的${field}：${before} → ${after}`,
    ja: (name: string, field: string, before: string, after: string) =>
      `結果 ${name} の${field}：${before} → ${after}`,
  },
  traceRuleAdded: {
    ko: (rule: string, condition: string) => `판정 규칙 추가: ${rule} · ${condition}`,
    en: (rule: string, condition: string) => `Assessment rule added: ${rule} · ${condition}`,
    "zh-Hans": (rule: string, condition: string) => `已添加评估规则：${rule} · ${condition}`,
    "zh-Hant": (rule: string, condition: string) => `已新增評估規則：${rule} · ${condition}`,
    ja: (rule: string, condition: string) => `判定ルールを追加：${rule} · ${condition}`,
  },
  traceRuleChanged: {
    ko: (field: string, before: string, after: string) =>
      `판정 규칙의 ${field}: ${before} → ${after}`,
    en: (field: string, before: string, after: string) =>
      `Assessment rule, ${field}: ${before} → ${after}`,
    "zh-Hans": (field: string, before: string, after: string) =>
      `评估规则的${field}：${before} → ${after}`,
    "zh-Hant": (field: string, before: string, after: string) =>
      `評估規則的${field}：${before} → ${after}`,
    ja: (field: string, before: string, after: string) =>
      `判定ルールの${field}：${before} → ${after}`,
  },
  traceLinkAdded: {
    ko: (name: string) => `요구사항 연결 추가: ${name}`,
    en: (name: string) => `Requirement link added: ${name}`,
    "zh-Hans": (name: string) => `已添加要求链接：${name}`,
    "zh-Hant": (name: string) => `已新增要求連結：${name}`,
    ja: (name: string) => `要件へのリンクを追加：${name}`,
  },
  traceLinkRemoved: {
    ko: (name: string) => `요구사항 연결 삭제: ${name}`,
    en: (name: string) => `Requirement link removed: ${name}`,
    "zh-Hans": (name: string) => `已移除要求链接：${name}`,
    "zh-Hant": (name: string) => `已移除要求連結：${name}`,
    ja: (name: string) => `要件へのリンクを削除：${name}`,
  },
  traceIgnoredColumn: {
    ko: (name: string) => `무시한 열: ${name}`,
    en: (name: string) => `Ignored column: ${name}`,
    "zh-Hans": (name: string) => `已忽略的列：${name}`,
    "zh-Hant": (name: string) => `已忽略的列：${name}`,
    ja: (name: string) => `無視した列：${name}`,
  },
  traceRow: {
    ko: (row: number) => ` (${row}번째 줄)`,
    en: (row: number) => ` (row ${row})`,
    "zh-Hans": (row: number) => `（第 ${row} 行）`,
    "zh-Hant": (row: number) => `（第 ${row} 行）`,
    ja: (row: number) => `（${row} 行目）`,
  },
  traceDuplicate: {
    ko: (ref: string, where: string) =>
      `${iga("같은 ID" + ref)} 파일에 두 번 이상 있습니다${where}.`,
    en: (ref: string, where: string) => `The same ID${ref} appears more than once${where}.`,
    "zh-Hans": (ref: string, where: string) => `同一个 ID${ref} 在文件中出现了不止一次${where}。`,
    "zh-Hant": (ref: string, where: string) => `同一個 ID${ref} 在檔案中出現了不止一次${where}。`,
    ja: (ref: string, where: string) => `同じ ID${ref} が、ファイル内に2回以上あります${where}。`,
  },
  traceInvalidRow: {
    ko: (row: string | number, ref: string) =>
      `${row}번째 줄${ref}의 값을 읽지 못했습니다. 칸의 형식이 맞는지 확인하세요.`,
    en: (row: string | number, ref: string) =>
      `Values in row ${row}${ref} could not be read. Check the field formats.`,
    "zh-Hans": (row: string | number, ref: string) =>
      `无法读取第 ${row} 行${ref} 的值。请检查各栏的格式。`,
    "zh-Hant": (row: string | number, ref: string) =>
      `無法讀取第 ${row} 行${ref} 的值。請檢查各欄的格式。`,
    ja: (row: string | number, ref: string) =>
      `${row} 行目${ref} の値を読み取れませんでした。欄の形式が合っているか確認してください。`,
  },
  traceKeyChanged: {
    ko: (ref: string, where: string) => `항목${ref}의 내부 키는 바꿀 수 없습니다${where}.`,
    en: (ref: string, where: string) => `The internal key of item${ref} cannot be changed${where}.`,
    "zh-Hans": (ref: string, where: string) => `项目${ref} 的内部键不能更改${where}。`,
    "zh-Hant": (ref: string, where: string) => `項目${ref} 的內部鍵不能更改${where}。`,
    ja: (ref: string, where: string) => `項目${ref} の内部キーは変更できません${where}。`,
  },
  traceDeleteMissing: {
    ko: (ref: string, where: string) => `${iga("지우려는 것" + ref)} 추적표에 없습니다${where}.`,
    en: (ref: string, where: string) =>
      `The deletion target${ref} is not in the trace table${where}.`,
    "zh-Hans": (ref: string, where: string) => `要删除的对象${ref} 不在追溯表中${where}。`,
    "zh-Hant": (ref: string, where: string) => `要刪除的物件${ref} 不在追溯表中${where}。`,
    ja: (ref: string, where: string) =>
      `削除しようとしている対象${ref} が、追跡表にありません${where}。`,
  },
  traceDeleteLinks: {
    ko: (ref: string) => `항목${ref}에는 아직 연결이 남아 있어 지울 수 없습니다.`,
    en: (ref: string) => `Item${ref} cannot be deleted while links remain.`,
    "zh-Hans": (ref: string) => `项目${ref} 仍有链接，无法删除。`,
    "zh-Hant": (ref: string) => `項目${ref} 仍有連結，無法刪除。`,
    ja: (ref: string) => `項目${ref} にはまだリンクが残っているため、削除できません。`,
  },
  traceDeleteRule: {
    ko: (ref: string) => `기준${ref}에는 판정 규칙이 남아 있어 지울 수 없습니다.`,
    en: (ref: string) => `Criterion${ref} cannot be deleted while an assessment rule remains.`,
    "zh-Hans": (ref: string) => `标准${ref} 仍有评估规则，无法删除。`,
    "zh-Hant": (ref: string) => `標準${ref} 仍有評估規則，無法刪除。`,
    ja: (ref: string) => `基準${ref} には判定ルールが残っているため、削除できません。`,
  },
  traceDeleteResult: {
    ko: (ref: string) => `항목${ref}에는 결과가 남아 있어 지울 수 없습니다.`,
    en: (ref: string) => `Item${ref} cannot be deleted while results remain.`,
    "zh-Hans": (ref: string) => `项目${ref} 仍有结果，无法删除。`,
    "zh-Hant": (ref: string) => `項目${ref} 仍有結果，無法刪除。`,
    ja: (ref: string) => `項目${ref} には結果が残っているため、削除できません。`,
  },
  traceDeleteAndUpdate: {
    ko: (ref: string, where: string) =>
      `${eulreul(ref.trim())} 같은 파일에서 고치면서 지우려 합니다${where}.`,
    en: (ref: string, where: string) => `The file both updates and deletes ${ref.trim()}${where}.`,
    "zh-Hans": (ref: string, where: string) =>
      `文件在更新 ${ref.trim()} 的同时又要删除它${where}。`,
    "zh-Hant": (ref: string, where: string) =>
      `檔案在更新 ${ref.trim()} 的同時又要刪除它${where}。`,
    ja: (ref: string, where: string) =>
      `同じファイルで ${ref.trim()} を更新しながら削除しようとしています${where}。`,
  },
  traceDeleteIncomplete: {
    ko: (where: string) => `삭제 줄에 종류나 ID가 비어 있습니다${where}.`,
    en: (where: string) => `The deletion row is missing a type or ID${where}.`,
    "zh-Hans": (where: string) => `删除行缺少类型或 ID${where}。`,
    "zh-Hant": (where: string) => `刪除行缺少類型或 ID${where}。`,
    ja: (where: string) => `削除の行に、種類または ID がありません${where}。`,
  },
  traceSpreadsheetId: {
    ko: (ref: string, where: string, detail: string) =>
      `${eunneun("ID" + ref)} 스프레드시트가 바꾼 것처럼 보입니다${where}. ${detail}`,
    en: (ref: string, where: string, detail: string) =>
      `ID${ref} appears to have been changed by a spreadsheet${where}. ${detail}`,
    "zh-Hans": (ref: string, where: string, detail: string) =>
      `ID${ref} 看起来被电子表格改动过${where}。${detail}`,
    "zh-Hant": (ref: string, where: string, detail: string) =>
      `ID${ref} 看起來被電子表格改動過${where}。${detail}`,
    ja: (ref: string, where: string, detail: string) =>
      `ID${ref} が、スプレッドシートによって変更されたようです${where}。${detail}`,
  },
  traceFormatUnsupported: {
    ko: (where: string) => `이 화면이 읽는 형식의 파일이 아닙니다${where}.`,
    en: (where: string) => `This file format is not supported by this view${where}.`,
    "zh-Hans": (where: string) => `此视图不支持这种文件格式${where}。`,
    "zh-Hant": (where: string) => `此檢視不支援這種檔案格式${where}。`,
    ja: (where: string) => `この画面が読み込める形式のファイルではありません${where}。`,
  },
  traceUnknownRowType: {
    ko: (where: string) => `줄의 종류를 알 수 없습니다${where}.`,
    en: (where: string) => `The row type is unknown${where}.`,
    "zh-Hans": (where: string) => `无法识别该行的类型${where}。`,
    "zh-Hant": (where: string) => `無法識別該行的類型${where}。`,
    ja: (where: string) => `行の種類が不明です${where}。`,
  },
  traceIdMissing: {
    ko: (where: string) => `ID가 비어 있는 줄이 있습니다${where}.`,
    en: (where: string) => `A row is missing its ID${where}.`,
    "zh-Hans": (where: string) => `有一行缺少 ID${where}。`,
    "zh-Hant": (where: string) => `有一行缺少 ID${where}。`,
    ja: (where: string) => `ID が空の行があります${where}。`,
  },
} as const;
