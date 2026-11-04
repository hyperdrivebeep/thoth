/** One short sentence for each reason the server can give when it cannot read a connected file.
 *  The codes are the values of ParserErrorCode (src/thoth/domain/enums.py); a test keeps the two in step. */
export const parserErrorMessages = {
  "parserError.AMBIGUOUS_PARSER_SELECTION": {
    ko: "이 파일을 읽는 방법을 하나로 정하지 못했습니다. 파일 형식과 확장자가 맞는지 확인해 주세요.",
    en: "THOTH could not decide how to read this file. Check that the file type and extension match.",
  },
  "parserError.PARSER_CAPABILITY_UNAVAILABLE": {
    ko: "이 파일을 읽는 데 필요한 기능을 쓸 수 없습니다. 설치 안내에서 선택 구성요소를 확인해 주세요.",
    en: "The reader this file needs is not available. Check the optional components in the install guide.",
  },
  "parserError.PARSER_DEADLINE": {
    ko: "파일을 읽는 데 정해진 시간이 지났습니다. 더 작은 파일로 나누어 다시 올려 주세요.",
    en: "Reading the file took longer than allowed. Split it into smaller files and upload again.",
  },
  "parserError.BYTE_HASH_MISMATCH": {
    ko: "올린 파일이 읽는 동안 바뀌었습니다. 파일을 다시 올려 주세요.",
    en: "The file changed while it was being read. Upload it again.",
  },
  "parserError.CORRUPT_DOCUMENT": {
    ko: "파일이 손상되었거나 형식이 맞지 않아 읽지 못했습니다. 다른 프로그램에서 열어 다시 저장한 뒤 올려 주세요.",
    en: "The file is damaged or not in the expected format. Open it in another program, save it again and upload it.",
  },
  "parserError.DIRTY_GIT_TARGET": {
    ko: "Git 저장소에 저장하지 않은 변경이 있습니다. 변경을 커밋한 뒤 다시 연결해 주세요.",
    en: "The Git repository has uncommitted changes. Commit them and connect again.",
  },
  "parserError.ENCRYPTED_DOCUMENT": {
    ko: "암호로 보호된 파일입니다. 암호를 푼 파일을 올려 주세요.",
    en: "The file is password protected. Upload a copy without the password.",
  },
  "parserError.EXTERNAL_LINK_IGNORED": {
    ko: "파일 안의 외부 링크는 따라가지 않았습니다. 링크의 내용이 필요하면 따로 연결해 주세요.",
    en: "Links to other places in the file were not followed. Connect the linked content separately if you need it.",
  },
  "parserError.FORMULA_NOT_RECALCULATED": {
    ko: "스프레드시트의 수식은 다시 계산하지 않고 저장된 값을 읽었습니다.",
    en: "Spreadsheet formulas were not recalculated; the stored values were read.",
  },
  "parserError.HIDDEN_CONTENT": {
    ko: "숨겨진 내용이 있어 그 부분은 읽지 않았습니다.",
    en: "The file has hidden content, which was not read.",
  },
  "parserError.MEDIA_TYPE_MISMATCH": {
    ko: "파일 확장자와 실제 내용의 형식이 다릅니다. 올바른 확장자로 저장해 다시 올려 주세요.",
    en: "The file extension does not match what is inside. Save it with the right extension and upload again.",
  },
  "parserError.OCR_REQUIRED": {
    ko: "글자가 이미지로만 들어 있어 읽지 못했습니다. 글자를 선택할 수 있는 파일로 바꿔 올려 주세요.",
    en: "The text is only an image, so it could not be read. Upload a version where the text can be selected.",
  },
  "parserError.OVERSIZED_ARCHIVE": {
    ko: "압축 파일 안의 내용이 너무 크거나 많아 읽지 않았습니다. 필요한 파일만 따로 올려 주세요.",
    en: "The archive holds too much content to read. Upload only the files you need.",
  },
  "parserError.PARTIAL_EXTRACTION": {
    ko: "일부만 읽을 수 있었습니다. 로그인이 필요하거나 스크립트로 그려지는 페이지는 저장한 파일(HTML·PDF)로 올려 주세요.",
    en: "Only part of it could be read. For a page that needs a login or is drawn by scripts, upload a saved copy (HTML or PDF).",
  },
  "parserError.TEXT_ENCODING_UNCONFIRMED": {
    ko: "글자 인코딩을 확인하지 못했습니다. 파일을 UTF-8로 저장해 다시 올려 주세요.",
    en: "The text encoding could not be confirmed. Save the file as UTF-8 and upload it again.",
  },
  "parserError.UNSUPPORTED_ELEMENT": {
    ko: "지원하지 않는 요소가 있어 그 부분은 읽지 않았습니다.",
    en: "The file has an element that is not supported, so that part was not read.",
  },
  "parserError.UNSUPPORTED_MEDIA_TYPE": {
    ko: "지원하지 않는 파일 형식입니다. 위의 지원 형식을 확인해 주세요.",
    en: "This file type is not supported. Check the supported types above.",
  },
  "parserError.UNTRUSTED_LOG_LINE": {
    ko: "신뢰할 수 없는 로그 줄은 읽지 않았습니다.",
    en: "Log lines that cannot be trusted were not read.",
  },
  "parserError.ZIP_SLIP": {
    ko: "압축 파일에 안전하지 않은 경로가 있어 거절했습니다. 파일을 확인해 주세요.",
    en: "The archive contains an unsafe path, so it was refused. Check the file.",
  },
  "parserError.unknown": {
    ko: "파일을 읽지 못했습니다. 아래 기술 정보를 함께 알려 주세요.",
    en: "The file could not be read. Include the technical details below when you report it.",
  },
  "sources.technicalDetails": { ko: "기술 정보", en: "Technical details" },
} as const;
