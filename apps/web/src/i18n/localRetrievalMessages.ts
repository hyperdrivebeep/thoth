import type { LanguageCode } from "./languageChoices";
import { LOCALE_TAGS } from "./languageChoices";
import { localRetrievalReasonZh, localRetrievalZh } from "./zh/localRetrievalMessagesZh";
import { localRetrievalReasonZhHant, localRetrievalZhHant } from "./zhHant/localRetrievalMessagesZhHant";
import { localRetrievalJa, localRetrievalReasonJa } from "./ja/localRetrievalMessagesJa";

/**
 * Copy for the "Local retrieval preparation" card, included by the central catalog.
 * Server reason codes are translated here as well; raw paths, stacks and server message
 * text never pass through this file.
 */
export const localRetrievalMessages = {
  "localRetrieval.title": { ko: "로컬 검색 준비", en: "Local retrieval preparation" },
  "localRetrieval.hostedOnly": {
    ko: "이 기능은 로컬 작업 공간에서만 쓸 수 있습니다. 심사 작업 공간에서는 아무것도 준비하지 않습니다.",
    en: "This is available only in a local workspace. Nothing is prepared in a review workspace.",
  },
  "localRetrieval.intro": {
    ko: "모델 파일과 로컬 실행 구성 요소를 설치하면 모든 프로젝트의 자료가 백그라운드에서 자동으로 색인되고, 연구가 뜻이 비슷한 문단도 함께 찾습니다. 색인이 준비되기 전이거나 이번 질문에서 쓸 수 없으면 기존 검색 방식 그대로입니다. 프로젝트별로 끌 수 있습니다.",
    en: "Once the model files and the local runtime component are installed, every project's sources are indexed automatically in the background and research also looks for passages with a similar meaning. Until an index is ready, or when it cannot be used for a question, the existing search method is used. You can switch it off per project.",
  },
  "localRetrieval.readFailed": { ko: "상태를 불러오지 못했습니다.", en: "The status could not be loaded." },
  "localRetrieval.unsupported": {
    ko: "이 서버는 로컬 검색 준비를 지원하지 않습니다.",
    en: "This server does not support local retrieval preparation.",
  },
  "localRetrieval.modelLabel": { ko: "고정 모델", en: "Fixed model" },
  "localRetrieval.runtimeNote": {
    ko: "모델 파일 외에 로컬 실행 구성 요소(선택 설치)가 추가로 필요합니다. 두 가지가 모두 있어야 색인을 준비할 수 있습니다.",
    en: "Besides the model files, an optional local runtime component is required. Both are needed before an index can be prepared.",
  },
  "localRetrieval.asset.MODEL_REQUIRED": { ko: "모델 파일 없음", en: "Model files missing" },
  "localRetrieval.asset.READY": { ko: "모델 파일 준비됨", en: "Model files ready" },
  "localRetrieval.asset.FAILED": { ko: "모델 파일 확인 실패", en: "Model file check failed" },
  "localRetrieval.dependencyAvailable": { ko: "실행 구성 요소: 있음", en: "Runtime component: available" },
  "localRetrieval.dependencyMissing": {
    ko: "실행 구성 요소: 없음. 파일 반입은 할 수 있지만 색인 준비는 할 수 없습니다.",
    en: "Runtime component: missing — files can be imported, but no index can be prepared.",
  },
  "localRetrieval.installRuntimeHelp": {
    ko: "설치 방법: THOTH 폴더에서 PowerShell로 아래 명령을 실행하세요(선택 설치, 약 110MB).",
    en: "To install it, run this command in PowerShell from the THOTH folder (optional, about 110 MB):",
  },
  "localRetrieval.installRuntimeRestart": {
    ko: "설치한 뒤 THOTH를 다시 시작하세요.",
    en: "After installing, restart THOTH.",
  },
  "localRetrieval.workerLoaded": { ko: "작업자: 로드됨", en: "Worker: loaded" },
  "localRetrieval.workerNotLoaded": { ko: "작업자: 로드 안 됨", en: "Worker: not loaded" },
  "localRetrieval.manageDenied": {
    ko: "이 작업 공간에서 모델 파일을 관리할 권한이 없습니다.",
    en: "You cannot manage model files in this workspace.",
  },
  "localRetrieval.importTitle": { ko: "로컬 폴더에서 가져오기", en: "Import from a local folder" },
  "localRetrieval.importHelp": {
    ko: "이미 받아 둔 모델 폴더를 검증한 뒤 복사합니다. 네트워크를 쓰지 않으며, 고정된 파일 목록과 다르면 거부합니다.",
    en: "Verifies an already downloaded model folder, then copies it. No network is used; anything that differs from the fixed file list is rejected.",
  },
  "localRetrieval.importPathLabel": { ko: "모델 폴더 경로", en: "Model folder path" },
  "localRetrieval.importButton": { ko: "검증해 가져오기", en: "Verify and import" },
  "localRetrieval.installTitle": { ko: "고정 파일 내려받아 설치", en: "Download the fixed files" },
  "localRetrieval.installNotice": {
    ko: "출처: 공개 모델 저장소의 고정된 버전만 받습니다. 모델 라이선스 고지는 파일과 함께 저장됩니다. 다른 주소나 패키지 설치는 지원하지 않습니다.",
    en: "Source: only the fixed revision from the public model repository is downloaded. The model license notice is stored with the files. Other addresses or package installs are not supported.",
  },
  "localRetrieval.installConfirm": {
    ko: "네트워크로 위 고정 파일을 내려받는 데 동의합니다",
    en: "I agree to download the fixed files above over the network",
  },
  "localRetrieval.installButton": { ko: "내려받아 설치", en: "Download and install" },
  "localRetrieval.transfer.COPYING": { ko: "복사 중", en: "Copying" },
  "localRetrieval.transfer.DOWNLOADING": { ko: "내려받는 중", en: "Downloading" },
  "localRetrieval.transfer.VERIFYING": { ko: "검증 중", en: "Verifying" },
  "localRetrieval.projectTitle": { ko: "이 프로젝트의 자료 색인", en: "Source index for this project" },
  "localRetrieval.noProject": {
    ko: "프로젝트를 열면 그 프로젝트의 자료 색인을 준비할 수 있습니다.",
    en: "Open a project to prepare its source index.",
  },
  "localRetrieval.enableLabel": { ko: "이 프로젝트에서 로컬 색인 사용", en: "Use local indexing in this project" },
  "localRetrieval.enableHelp": {
    ko: "기본값은 켜짐입니다. 끄면 이 프로젝트는 색인을 만들지 않고 기존 검색만 씁니다. 색인은 백그라운드에서 자동으로 만들어지며, '색인 준비'로 지금 바로 시작할 수도 있습니다.",
    en: "On by default. Turn it off and this project builds no index and uses the existing search only. The index is built automatically in the background; 'Prepare index' starts it right away.",
  },
  "localRetrieval.prepareNotAllowed": { ko: "이 프로젝트의 색인을 바꿀 권한이 없습니다.", en: "You cannot change this project's index." },
  "localRetrieval.configureConflict": {
    ko: "설정이 다른 곳에서 바뀌어 저장하지 않았습니다. 최신 상태를 다시 불러왔습니다.",
    en: "The setting changed elsewhere, so nothing was saved. The latest state was reloaded.",
  },
  "localRetrieval.configureFailed": { ko: "설정을 저장하지 못했습니다.", en: "The setting could not be saved." },
  "localRetrieval.index.OFF": { ko: "꺼짐", en: "Off" },
  "localRetrieval.index.NO_SOURCES": { ko: "색인할 자료 없음", en: "No sources to index" },
  "localRetrieval.index.INDEX_PENDING": { ko: "색인 대기", en: "Index pending" },
  "localRetrieval.index.INDEXING": { ko: "색인 중", en: "Indexing" },
  "localRetrieval.index.READY": { ko: "색인 준비됨", en: "Index ready" },
  "localRetrieval.index.FAILED": { ko: "일부 실패", en: "Some failed" },
  "localRetrieval.index.CANCEL_REQUESTED": { ko: "취소 요청됨", en: "Cancel requested" },
  "localRetrieval.prepareButton": { ko: "색인 준비", en: "Prepare index" },
  "localRetrieval.prepareBlockedOff": { ko: "먼저 이 프로젝트에서 로컬 색인을 켜세요.", en: "Turn on local indexing in this project first." },
  "localRetrieval.prepareBlockedAsset": { ko: "모델 파일이 준비돼야 색인을 준비할 수 있습니다.", en: "The model files must be ready before an index can be prepared." },
  "localRetrieval.prepareBlockedDependency": {
    ko: "실행 구성 요소가 없어 색인 준비를 할 수 없습니다.",
    en: "No index can be prepared because the runtime component is missing.",
  },
  "localRetrieval.readyNote": {
    ko: "모든 자료의 색인이 준비되면 연구에서 뜻이 비슷한 문단도 함께 찾습니다. 이번 질문에서 쓸 수 없으면 기존 검색 방식 그대로입니다.",
    en: "Once every source is indexed, research also looks for passages with a similar meaning. When it cannot be used for a question, the existing search method is used as before.",
  },
  "localRetrieval.outdatedIndex": {
    ko: "검색 준비 방식이 바뀌어 색인을 다시 만들고 있습니다. 자동으로 진행되며, '색인 준비'로 지금 바로 시작할 수도 있습니다. 모델 파일은 다시 받지 않아도 됩니다.",
    en: "The way passages are prepared for search changed, so the index is being rebuilt. This happens automatically; 'Prepare index' starts it right away. The model files do not need to be downloaded again.",
  },
  "localRetrieval.jobsTitle": { ko: "색인 작업", en: "Index jobs" },
  "localRetrieval.job.PENDING": { ko: "대기", en: "Pending" },
  "localRetrieval.job.RUNNING": { ko: "실행 중", en: "Running" },
  "localRetrieval.job.READY": { ko: "준비됨", en: "Ready" },
  "localRetrieval.job.FAILED": { ko: "실패", en: "Failed" },
  "localRetrieval.job.CANCELLED": { ko: "취소됨", en: "Cancelled" },
  "localRetrieval.job.STALE": { ko: "자료 바뀜", en: "Source changed" },
  "localRetrieval.retryButton": { ko: "다시 시도", en: "Retry" },
  "localRetrieval.cancelButton": { ko: "취소 요청", en: "Request cancel" },
  "localRetrieval.op.import": { ko: "폴더 가져오기", en: "Folder import" },
  "localRetrieval.op.install": { ko: "내려받아 설치", en: "Download and install" },
  "localRetrieval.op.prepare": { ko: "색인 준비", en: "Index preparation" },
  "localRetrieval.op.retry": { ko: "작업 다시 시도", en: "Job retry" },
  "localRetrieval.opStarting": { ko: "시작 요청 중", en: "Starting" },
  "localRetrieval.opRunning": { ko: "진행 중", en: "In progress" },
  "localRetrieval.opCancelRequested": {
    ko: "취소 요청됨. 서버가 실제로 멈췄는지 확인하는 중입니다.",
    en: "Cancel requested — checking whether the server actually stopped.",
  },
  "localRetrieval.opCancelRejected": { ko: "취소 요청을 서버가 받아들이지 않았습니다. 작업은 계속됩니다.", en: "The server did not accept the cancel request. The work continues." },
  "localRetrieval.opCancelUnconfirmed": { ko: "취소 요청이 전달됐는지 확인하지 못했습니다.", en: "It could not be confirmed that the cancel request arrived." },
  "localRetrieval.opSucceeded": { ko: "완료", en: "Completed" },
  "localRetrieval.opFailed": { ko: "실패", en: "Failed" },
  "localRetrieval.opCancelled": { ko: "취소됨. 서버가 작업 종료를 확인했습니다.", en: "Cancelled — the server confirmed that the work stopped." },
  "localRetrieval.opRejected": { ko: "서버가 작업을 시작하지 않았습니다.", en: "The server did not start the work." },
  "localRetrieval.opUnconfirmed": {
    ko: "결과를 확인하지 못했습니다. 서버 작업은 계속되고 있을 수 있습니다. 같은 작업의 결과를 확인하거나 새로 실행할 수 있습니다.",
    en: "The result could not be confirmed. The server may still be working. You can check the same work's result or run it again as new work.",
  },
  "localRetrieval.opStartUnconfirmed": {
    ko: "시작 요청이 서버에 닿았는지 확인하지 못했습니다. '결과 확인'은 같은 요청으로 다시 묻고, '새로 실행'은 새 작업을 만듭니다.",
    en: "It could not be confirmed that the start request reached the server. 'Check result' asks again with the same request; 'Run as new' creates new work.",
  },
  "localRetrieval.opAbandoned": { ko: "화면이 바뀌어 결과 확인을 멈췄습니다.", en: "The screen changed, so result checking stopped." },
  "localRetrieval.confirmResultButton": { ko: "결과 확인", en: "Check result" },
  "localRetrieval.newRunButton": { ko: "새로 실행", en: "Run as new" },
  "localRetrieval.operationLabel": { ko: "서버 작업", en: "Server work" },
  "localRetrieval.reasonUnknown": { ko: "알 수 없는 이유", en: "Unknown reason" },
} as const;

export type LocalRetrievalMessageKey = keyof typeof localRetrievalMessages;

export function localRetrievalText(key: LocalRetrievalMessageKey, locale: LanguageCode): string {
  return locale === "ko" || locale === "en" ? localRetrievalMessages[key][locale] : { "zh-Hans": localRetrievalZh, "zh-Hant": localRetrievalZhHant, ja: localRetrievalJa }[locale][key];
}

/** Server reason codes shown to people. Unknown codes fall back to a generic line plus the code itself. */
export const localRetrievalReasonMessages = {
  RESOURCE_AUTHORITY_CHANGED: { ko: "작업 중 권한이나 로그인 상태가 바뀌었습니다.", en: "Access or session authority changed during the operation." },
  LOCAL_RETRIEVAL_FAILED: { ko: "로컬 준비 작업을 마치지 못했습니다.", en: "Local preparation could not finish." },
  LOCAL_RETRIEVAL_PROJECT_MISSING: { ko: "프로젝트를 찾을 수 없습니다.", en: "The project could not be found." },
  EMBEDDING_NOT_PREPARED: { ko: "모델 실행기가 준비되지 않았습니다. 색인 준비를 다시 요청하세요.", en: "The model worker is not prepared. Request index preparation again." },
  EMBEDDING_PROTOCOL_FAILED: { ko: "모델 실행기의 응답 형식을 확인하지 못했습니다.", en: "The model worker returned an invalid response." },
  EMBEDDING_JOB_NOT_FOUND: { ko: "지정한 색인 작업을 찾을 수 없습니다.", en: "The specified index job could not be found." },
  EMBEDDING_REQUEST_DEADLINE: { ko: "색인 준비의 전체 제한 시간을 넘었습니다.", en: "Index preparation exceeded its total time limit." },
  MODEL_NOT_INSTALLED: { ko: "모델 파일이 설치돼 있지 않습니다.", en: "The model files are not installed." },
  MODEL_HASH_MISMATCH: { ko: "모델 파일이 고정된 내용과 다릅니다.", en: "The model files differ from the fixed content." },
  EMBEDDING_DEPENDENCY_UNAVAILABLE: { ko: "로컬 실행 구성 요소가 없습니다.", en: "The local runtime component is missing." },
  EMBEDDING_LOAD_FAILED: { ko: "모델을 메모리에 올리지 못했습니다.", en: "The model could not be loaded." },
  EMBEDDING_TIMEOUT: { ko: "제한 시간 안에 끝나지 않았습니다.", en: "It did not finish within the time limit." },
  EMBEDDING_FAILED: { ko: "색인 작업이 실패했습니다.", en: "The indexing work failed." },
  EMBEDDING_SOURCE_CHANGED: { ko: "자료가 바뀌어 이 색인은 더 이상 맞지 않습니다.", en: "The source changed, so this index no longer matches." },
  EMBEDDING_SOURCE_CORRUPT: { ko: "저장된 자료 내용이 기록과 맞지 않습니다.", en: "The stored source content does not match its record." },
  EMBEDDING_CONFIG_CONFLICT: { ko: "설정이 다른 곳에서 바뀌었습니다.", en: "The setting changed elsewhere." },
  EMBEDDING_DISABLED: { ko: "이 프로젝트에서 로컬 색인이 꺼져 있습니다.", en: "Local indexing is off in this project." },
  EMBEDDING_PROJECT_SCOPE_DENIED: { ko: "이 프로젝트에 대한 권한이 없습니다.", en: "You do not have access to this project." },
  EMBEDDING_INDEX_ACTOR_CHANGED: { ko: "색인을 켠 사람과 달라 실행할 수 없습니다.", en: "A different person turned the index on, so this cannot run." },
  EMBEDDING_JOB_NOT_PENDING: { ko: "이 작업은 이미 처리됐거나 대기 상태가 아닙니다.", en: "This job was already handled or is not pending." },
  EMBEDDING_RETRY_CONFLICT: { ko: "작업 상태가 바뀌어 다시 시도하지 않았습니다.", en: "The job state changed, so it was not retried." },
  INDEX_PENDING: { ko: "색인이 아직 준비되지 않았습니다.", en: "The index is not ready yet." },
  INDEX_STALE: { ko: "색인이 현재 자료와 맞지 않습니다.", en: "The index does not match the current sources." },
  INDEX_INCOMPLETE: { ko: "색인이 완전하지 않습니다.", en: "The index is incomplete." },
  PASSAGE_TOO_LONG: { ko: "자료 구간이 모델의 길이 제한을 넘습니다.", en: "The source passage exceeds the model length limit." },
  MODEL_SOURCE_MISSING: { ko: "가져올 원본 폴더가 없습니다.", en: "The source folder to import does not exist." },
  QUERY_TOO_LONG: { ko: "입력이 너무 깁니다.", en: "The input is too long." },
  MODEL_ASSET_PATH_INVALID: { ko: "폴더 경로를 쓸 수 없습니다. 실제 복사본 폴더의 절대 경로를 입력하세요.", en: "The folder path cannot be used. Enter the absolute path of a real copied folder." },
  MODEL_ASSET_BUSY: { ko: "같은 모델을 이미 설치하는 중입니다.", en: "The same model is already being installed." },
  MODEL_ASSET_CONSENT_REQUIRED: { ko: "네트워크 다운로드 동의가 없어 설치하지 않았습니다.", en: "Not installed: the network download was not confirmed." },
  MODEL_DOWNLOAD_FAILED: { ko: "내려받기에 실패했습니다. 인터넷 연결을 확인하세요.", en: "The download failed. Check the internet connection." },
  MODEL_MANIFEST_CHANGED: { ko: "화면의 파일 목록이 서버의 고정 목록과 달라 다시 확인이 필요합니다.", en: "The file list on screen differs from the server's fixed list; confirm again." },
  LOCAL_RETRIEVAL_SCOPE_DENIED: { ko: "모델 파일을 관리할 권한이 없습니다.", en: "You cannot manage model files." },
  MODEL_ASSET_TIMEOUT: { ko: "파일 준비 제한 시간을 넘었습니다.", en: "File preparation exceeded its time limit." },
  MODEL_ASSET_CLEANUP_UNCONFIRMED: { ko: "파일 작업 종료를 확인하지 못했습니다. 상태를 다시 확인하세요.", en: "File work termination is unconfirmed. Check its status again." },
  MODEL_ASSET_IO_FAILED: { ko: "로컬 파일을 읽거나 저장하지 못했습니다.", en: "Local files could not be read or saved." },
  MODEL_DOWNLOAD_DESTINATION_DENIED: { ko: "허용된 파일 제공처가 아니어서 중단했습니다.", en: "Stopped: the destination is not an allowed file host." },
  MODEL_DOWNLOAD_REDIRECT_LIMIT: { ko: "파일 제공처의 이동 횟수가 제한을 넘었습니다.", en: "The file host exceeded the redirect limit." },
  LOCAL_RETRIEVAL_LOCAL_ONLY: { ko: "로컬 작업 공간에서만 사용할 수 있습니다.", en: "Only available in a local workspace." },
  EMBEDDING_INDEX_BUSY: { ko: "다른 색인 작업이 진행 중입니다.", en: "Another index operation is running." },
  EMBEDDING_CLEANUP_UNCONFIRMED: { ko: "모델 작업 종료를 확인하지 못했습니다.", en: "Model worker termination is unconfirmed." },
  OPERATION_TIMEOUT: { ko: "서버 작업이 제한 시간 안에 끝나지 않았습니다.", en: "The server work did not finish within its time limit." },
  CALLER_CANCELLED: { ko: "요청에 따라 취소됐습니다.", en: "Cancelled as requested." },
} as const;

export type LocalRetrievalReasonCode = keyof typeof localRetrievalReasonMessages;

export function isKnownLocalRetrievalReason(code: string): code is LocalRetrievalReasonCode {
  return Object.hasOwn(localRetrievalReasonMessages, code);
}

/** A code is shown as is only when it looks like a code; anything else (paths, messages) is hidden. */
const codeShape = /^[A-Z][A-Z0-9_]{2,79}$/;

export function localRetrievalReason(code: string | null | undefined, locale: LanguageCode): string {
  if (!code) return localRetrievalText("localRetrieval.reasonUnknown", locale);
  if (isKnownLocalRetrievalReason(code)) {
    return locale === "ko" || locale === "en" ? localRetrievalReasonMessages[code][locale] : { "zh-Hans": localRetrievalReasonZh, "zh-Hant": localRetrievalReasonZhHant, ja: localRetrievalReasonJa }[locale][code];
  }
  const shown = codeShape.test(code) ? ` (${code})` : "";
  return `${localRetrievalText("localRetrieval.reasonUnknown", locale)}${shown}`;
}

const localeTag = (locale: LanguageCode) => LOCALE_TAGS[locale];

/** "123.6 MB · 129,606,142 bytes": the rounded size and the exact byte count. */
export function localRetrievalBytes(bytes: number, locale: LanguageCode): string {
  const exact = new Intl.NumberFormat(localeTag(locale)).format(bytes);
  const megabytes = (bytes / 1_000_000).toLocaleString(localeTag(locale), { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const unit = { ko: "바이트", en: "bytes", "zh-Hans": "字节", "zh-Hant": "位元組", ja: "バイト" }[locale];
  return `${megabytes} MB · ${exact} ${unit}`;
}

export function localRetrievalCounts(
  counts: { ready: number; total: number; pending: number; failed: number }, locale: LanguageCode,
): string {
  const n = (value: number) => new Intl.NumberFormat(localeTag(locale)).format(value);
  return {
    ko: `준비 ${n(counts.ready)} / 전체 ${n(counts.total)} · 대기 ${n(counts.pending)} · 실패 ${n(counts.failed)}`,
    en: `Ready ${n(counts.ready)} of ${n(counts.total)} · pending ${n(counts.pending)} · failed ${n(counts.failed)}`,
    "zh-Hans": `就绪 ${n(counts.ready)} / 共 ${n(counts.total)} · 待处理 ${n(counts.pending)} · 失败 ${n(counts.failed)}`,
    "zh-Hant": `就緒 ${n(counts.ready)} / 共 ${n(counts.total)} · 待處理 ${n(counts.pending)} · 失敗 ${n(counts.failed)}`,
    ja: `準備完了 ${n(counts.ready)} / 全 ${n(counts.total)} · 待機 ${n(counts.pending)} · 失敗 ${n(counts.failed)}`,
  }[locale];
}

export function localRetrievalTransfer(
  transfer: { state: "COPYING" | "DOWNLOADING" | "VERIFYING"; transferred_bytes: number; total_bytes: number }, locale: LanguageCode,
): string {
  const n = (value: number) => new Intl.NumberFormat(localeTag(locale)).format(value);
  const label = localRetrievalText(`localRetrieval.transfer.${transfer.state}`, locale);
  const unit = { ko: "바이트", en: "bytes", "zh-Hans": "字节", "zh-Hant": "位元組", ja: "バイト" }[locale];
  return `${label} · ${n(transfer.transferred_bytes)} / ${n(transfer.total_bytes)} ${unit}`;
}
