import type { ColumnOf } from "../withColumns";
import type { parserErrorMessages } from "../parserErrorMessages";

export const parserErrorZh: ColumnOf<typeof parserErrorMessages> = {
  "parserError.AMBIGUOUS_PARSER_SELECTION":
    "THOTH 无法确定如何读取此文件。请检查文件类型和扩展名是否一致。",
  "parserError.PARSER_CAPABILITY_UNAVAILABLE":
    "读取此文件所需的读取器不可用。请查看安装指南中的可选组件。",
  "parserError.PARSER_DEADLINE":
    "读取文件的时间超过了允许的时长。请将其拆分为较小的文件后重新上传。",
  "parserError.BYTE_HASH_MISMATCH": "文件在读取过程中发生了变化。请重新上传。",
  "parserError.CORRUPT_DOCUMENT":
    "文件已损坏或格式不符合预期。请用其他程序打开，重新保存后再上传。",
  "parserError.DIRTY_GIT_TARGET": "Git 仓库中有未提交的更改。请提交后重新连接。",
  "parserError.ENCRYPTED_DOCUMENT": "文件受密码保护。请上传去除密码的副本。",
  "parserError.EXTERNAL_LINK_IGNORED":
    "文件中指向其他位置的链接没有被跟进。如需这些内容，请单独连接。",
  "parserError.FORMULA_NOT_RECALCULATED": "电子表格公式没有重新计算，读取的是已存储的值。",
  "parserError.HIDDEN_CONTENT": "文件含有隐藏内容，未被读取。",
  "parserError.MEDIA_TYPE_MISMATCH": "文件扩展名与实际内容不符。请使用正确的扩展名保存后重新上传。",
  "parserError.OCR_REQUIRED": "文字只是图片，因此无法读取。请上传可选中文字的版本。",
  "parserError.OVERSIZED_ARCHIVE": "压缩包内容过多，无法读取。请只上传你需要的文件。",
  "parserError.PARTIAL_EXTRACTION":
    "只读取了一部分。对于需要登录或由脚本绘制的页面，请上传保存的副本（HTML 或 PDF）。",
  "parserError.TEXT_ENCODING_UNCONFIRMED": "无法确认文本编码。请将文件另存为 UTF-8 后重新上传。",
  "parserError.UNSUPPORTED_ELEMENT": "文件中含有不受支持的元素，该部分未被读取。",
  "parserError.UNSUPPORTED_MEDIA_TYPE": "不支持此文件类型。请查看上面列出的受支持类型。",
  "parserError.UNTRUSTED_LOG_LINE": "无法信任的日志行没有被读取。",
  "parserError.ZIP_SLIP": "压缩包中含有不安全的路径，因此被拒绝。请检查该文件。",
  "parserError.unknown": "无法读取该文件。报告问题时请附上下面的技术详情。",
  "sources.technicalDetails": "技术详情",
};
