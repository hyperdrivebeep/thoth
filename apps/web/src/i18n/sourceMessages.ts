export const sourceMessages = {
  "sources.fileUploadStalled": {
    ko: "파일을 보내는 중 2분 동안 진행이 없어 멈췄습니다. 연결을 확인하고 다시 시도하세요. 연결된 자료는 없습니다.",
    en: "No data was sent for 2 minutes, so the upload stopped. Check your connection and try again. No source was connected.",
  },
  "sources.fileUploadCancel": { ko: "업로드 취소", en: "Cancel upload" },
  "sources.fileUploadCancelled": {
    ko: "업로드를 취소했습니다. 연결된 자료는 없습니다.",
    en: "Upload cancelled. No source was connected.",
  },
  "sources.fileTitle": {
    ko: "파일 자료 연결",
    en: "Connect source files",
  },
  "sources.fileTypes": {
    ko: "HWPX · PDF · DOCX · XLSX · CSV · JSON · MD · TXT · YAML · HTML 파일을 올리고, 자료의 성격과 사용 범위를 함께 기록합니다.",
    en: "Upload HWPX, PDF, DOCX, XLSX, CSV, JSON, MD, TXT, YAML, or HTML files and record their authority and usage scope.",
  },
  "sources.hostedUpload": {
    ko: " 업로드한 파일은 이 심사 프로젝트에서 사용됩니다.",
    en: " Uploaded files are used in this review project.",
  },
  "sources.localUpload": {
    ko: " 파일은 이 컴퓨터의 THOTH 작업 공간에 준비됩니다.",
    en: " Files are staged in this computer's THOTH workspace.",
  },
  "sources.chooseFiles": {
    ko: "파일 선택…",
    en: "Choose files…",
  },
  "sources.browse": { ko: "찾아보기", en: "Browse" },
  "sources.fileLabel": {
    ko: "연결할 파일 자료",
    en: "Source files to connect",
  },
  "sources.authority": {
    ko: "자료 성격",
    en: "Source authority",
  },
  "sources.scope": {
    ko: "사용 범위",
    en: "Usage scope",
  },
  "sources.scopeLabel": {
    ko: "자료 사용 범위",
    en: "Source usage scope",
  },
  "sources.chooseScope": {
    ko: "사용 범위를 선택하세요",
    en: "Choose a usage scope",
  },
  "sources.projectShared": {
    ko: "이 프로젝트에서 함께 사용",
    en: "Shared within this project",
  },
  "sources.explicitGrants": {
    ko: "명시적으로 허용된 작업만",
    en: "Explicitly permitted tasks only",
  },
  "sources.connectFailed": {
    ko: "자료를 연결하지 못했습니다",
    en: "The source was not connected",
  },
  "sources.scopeHelp": {
    ko: "'이 프로젝트에서 함께 사용'은 이 프로젝트의 모든 작업이 이 자료를 근거로 쓸 수 있게 합니다. '명시적으로 허용된 작업만'은 따로 허용한 작업에서만 쓰게 하는 범위지만, 작업에 허용을 주는 화면이 아직 없습니다. 지금은 어느 쪽을 골라도 이 프로젝트의 조사가 이 자료를 읽을 수 있고, 고른 범위는 자료 기록에만 남습니다. 보통은 '이 프로젝트에서 함께 사용'을 고르세요.",
    en: "'Shared within this project' lets every task in this project use the source as evidence. 'Explicitly permitted tasks only' is meant to limit it to tasks that were given permission, but there is no screen yet for giving a task permission. For now, research in this project can read the source whichever you choose, and the choice is only kept in the source's record. 'Shared within this project' is the usual choice.",
  },
  "sources.connected": {
    ko: "자료 연결이 완료되었습니다. 분석은 새 질문이나 지시로 시작합니다.",
    en: "Sources are connected. Start analysis with a new question or instruction.",
  },
  "sources.unclassified": {
    ko: "자료 성격 미분류",
    en: "Authority unclassified",
  },
  "sources.informal": {
    ko: "비공식 참고자료",
    en: "Informal reference",
  },
  "sources.official": {
    ko: "공식자료",
    en: "Official source",
  },
  "sources.approved": {
    ko: "승인본",
    en: "Approved version",
  },
  "sources.superseded": {
    ko: "대체된 자료",
    en: "Superseded source",
  },
  "sources.inadmissible": {
    ko: "판단 사용 불가",
    en: "Not admissible for assessment",
  },
  "sources.beforeCutoff": {
    ko: "기준시점 이전",
    en: "Before the cutoff",
  },
  "sources.afterCutoff": {
    ko: "기준시점 이후",
    en: "After the cutoff",
  },
  "sources.timeUnknown": {
    ko: "시점 미확인",
    en: "Time unverified",
  },
  "sources.prohibited": {
    ko: "사용 금지 맥락",
    en: "Prohibited context",
  },
  "web.allowed": {
    ko: "허용함",
    en: "Allowed",
  },
  "web.denied": {
    ko: "거부함",
    en: "Denied",
  },
  "web.lookup": {
    ko: "공개 웹 자료 조회",
    en: "Public web retrieval",
  },
  "web.undecided": {
    ko: "아직 고르지 않았습니다",
    en: "Not selected yet",
  },
  "web.consentScope": {
    ko: "허용: 연결한 파일 다음에, 프로젝트에서 등록한 사이트만 찾습니다. 지금 허용해도 자동 검색은 없습니다. 거부: 웹 자료 조회만 거부합니다. 모델 호출은 계속됩니다.",
    en: "Allow: after connected files, research may use only sites registered in the project. This does not start a search. Deny: web retrieval is disabled; model calls continue.",
  },
  "web.permittedSources": {
    ko: "허용할 공개 웹 출처",
    en: "Permitted public web sources",
  },
  "web.registerFirst": {
    ko: "조회할 수 있는 도메인을 먼저 등록하세요. 저장하거나 켜는 것만으로 검색이 시작되지는 않습니다.",
    en: "Register permitted domains first. Saving or enabling them does not start a search.",
  },
  "web.allowInSettings": { ko: "설정에서 허용", en: "Allow in settings" },
  "web.deniedNext": {
    ko: "허용하면 여기서 조회할 도메인을 등록할 수 있습니다.",
    en: "Once it is allowed, you can register the domains to look up here.",
  },
  "web.workspaceDenied": {
    ko: "이 작업 공간에서는 인터넷 사용을 허용하지 않았습니다.",
    en: "Internet access is not allowed for this workspace.",
  },
  "web.domainLabel": {
    ko: "허용할 공개 웹 도메인",
    en: "Permitted public web domain",
  },
  "web.domainExample": {
    ko: "예: arxiv.org",
    en: "For example: arxiv.org",
  },
  "web.addDomain": {
    ko: "허용 도메인 추가",
    en: "Add permitted domain",
  },
  "web.useDomains": {
    ko: "등록한 도메인의 공개 웹 사용",
    en: "Use public web sources from registered domains",
  },
  "web.reapply": {
    ko: "저장된 웹 설정 다시 적용",
    en: "Reapply saved web settings",
  },
  "archive.show": {
    ko: "보관한 프로젝트 보기",
    en: "View archived projects",
  },
  "archive.loading": {
    ko: "보관한 프로젝트를 읽는 중…",
    en: "Loading archived projects…",
  },
  "archive.failed": {
    ko: "보관한 프로젝트를 읽지 못했습니다. 없다는 뜻은 아닙니다.",
    en: "Archived projects could not be read. This does not mean none exist.",
  },
  "archive.empty": {
    ko: "보관한 프로젝트가 없습니다.",
    en: "No archived projects.",
  },
  "archive.restore": {
    ko: "되살리기",
    en: "Restore",
  },
  "archive.restoreFailed": {
    ko: "되살리지 못했습니다.",
    en: "Could not restore the project.",
  },
  "archive.archiveFailed": {
    ko: "프로젝트를 보관하지 못했습니다. 다시 시도해 주세요.",
    en: "The project could not be archived. Try again.",
  },
  "archive.revisionChanged": {
    ko: "프로젝트가 그 사이 바뀌었습니다. 목록을 다시 읽었으니 다시 시도해 주세요.",
    en: "The project changed in the meantime. The list was read again; please try again.",
  },
  "archive.delete": { ko: "영구 삭제", en: "Delete permanently" },
  "archive.deleteCancel": { ko: "취소", en: "Cancel" },
  "archive.deleteClose": { ko: "닫기", en: "Close" },
  "archive.deleteRetry": { ko: "다시 시도", en: "Try again" },
  "archive.deleting": { ko: "삭제하는 중…", en: "Deleting…" },
  "archive.deleteWarning": { ko: "복구할 수 없습니다.", en: "This cannot be undone." },
  "archive.deleteRemoves": {
    ko: "지워지는 것: 이 프로젝트의 대화, 자료 기록, 업로드한 원본 파일.",
    en: "Deleted: this project's conversations, source records, and uploaded original files.",
  },
  "archive.deleteKeeps": {
    ko: "남는 것: 다른 프로젝트와 공유하는 파일(내용이 같은 파일은 한 번만 저장됩니다).",
    en: "Kept: files shared with other projects (identical files are stored only once).",
  },
  "archive.deleteTypeName": {
    ko: "계속하려면 프로젝트 이름을 그대로 입력하세요.",
    en: "To continue, type the project name exactly.",
  },
  "archive.deleteNameLabel": { ko: "프로젝트 이름 확인", en: "Confirm project name" },
  "archive.deleteDone": {
    ko: "프로젝트를 영구 삭제했습니다.",
    en: "The project was permanently deleted.",
  },
  "archive.deleteLeftover": {
    ko: "프로젝트는 삭제했지만 일부 파일을 지우지 못했습니다. 다시 시도하면 마저 지웁니다.",
    en: "The project was deleted, but some files could not be removed. Try again to finish removing them.",
  },
  "archive.deleteNotArchived": {
    ko: "보관한 프로젝트만 영구 삭제할 수 있습니다.",
    en: "Only an archived project can be deleted permanently.",
  },
  "archive.deleteNameMismatch": {
    ko: "프로젝트 이름이 일치하지 않습니다.",
    en: "The project name does not match.",
  },
  "archive.deleteActive": {
    ko: "이 프로젝트의 작업이 아직 실행 중입니다. 끝난 뒤 다시 시도하세요.",
    en: "Work in this project is still running. Try again after it finishes.",
  },
  "archive.deleteLocalOnly": {
    ko: "검토용 호스팅 환경에서는 프로젝트를 영구 삭제할 수 없습니다.",
    en: "Projects cannot be deleted permanently in the hosted review environment.",
  },
  "archive.deleteDenied": {
    ko: "이 프로젝트를 영구 삭제할 권한이 없습니다.",
    en: "You do not have permission to delete this project permanently.",
  },
  "archive.deleteNotFound": {
    ko: "프로젝트를 찾을 수 없습니다. 이미 삭제됐을 수 있어 목록을 새로 읽습니다.",
    en: "The project was not found. It may already be deleted, so the list is being refreshed.",
  },
  "archive.deleteFailed": {
    ko: "프로젝트를 영구 삭제하지 못했습니다. 다시 시도하세요.",
    en: "The project could not be deleted permanently. Try again.",
  },
  "credential.connections": {
    ko: "계정 연결",
    en: "Account connections",
  },
  "credential.registrationNotice": {
    ko: "지원되는 계정 로그인 절차를 시작하거나 API 키를 등록합니다. 키 발급 콘솔 방문만으로 THOTH 연결은 완료되지 않습니다.",
    en: "Start a supported account login or register an API key. Visiting the key console alone does not connect THOTH.",
  },
  "credential.methodsFailed": {
    ko: "계정 연결 방법을 확인하지 못했습니다. 다시 확인하세요.",
    en: "Connection methods could not be checked. Try again.",
  },
  "setupIssue.corrupt": {
    ko: "기존 작업 공간 설정 파일이 손상되었습니다. 신규 설정으로 취급하거나 원본을 덮어쓰지 않습니다.",
    en: "The existing workspace settings file is corrupt. It will not be treated as a new setup or overwritten.",
  },
  "setupIssue.unreadable": {
    ko: "기존 작업 공간 설정 파일을 읽지 못했습니다. 파일 접근 상태를 확인하기 전에는 새 설정을 저장하지 않습니다.",
    en: "The workspace settings file could not be read. New settings will not be saved before file access is checked.",
  },
  "setupIssue.unknown": {
    ko: "작업 공간 설정 상태를 확인하지 못했습니다. 기존 설정을 새 설정으로 대체하지 않습니다.",
    en: "The workspace settings state could not be verified. Existing settings will not be replaced.",
  },
} as const;
