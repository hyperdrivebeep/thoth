export const recordDialogsMessages = {
  "traceImport.item": {
    "ko": "항목",
    "en": "Item"
  },
  "traceImport.link": {
    "ko": "연결",
    "en": "Link"
  },
  "traceImport.rule": {
    "ko": "판정 규칙",
    "en": "Assessment rule"
  },
  "traceImport.result": {
    "ko": "결과",
    "en": "Result"
  },
  "traceImport.failed": {
    "ko": "파일을 미리보기하지 못했습니다. 아무것도 저장되지 않았습니다.",
    "en": "The file could not be previewed. Nothing was saved."
  },
  "traceImport.mode": {
    "ko": "들여오는 방식",
    "en": "Import mode"
  },
  "traceImport.create": {
    "ko": "새로 만들기 (추적표가 없는 프로젝트)",
    "en": "Create new table (project has no trace table)"
  },
  "traceImport.update": {
    "ko": "기존 표 고치기 (내보낸 파일을 고친 것)",
    "en": "Update existing table (edited exported file)"
  },
  "traceImport.chooseFile": {
    "ko": "CSV 파일 선택",
    "en": "Choose CSV file"
  },
  "traceImport.working": {
    "ko": "처리 중…",
    "en": "Processing…"
  },
  "traceImport.preview": {
    "ko": "들여오기 미리보기",
    "en": "Preview import"
  },
  "traceImport.notSaved": {
    "ko": "미리보기 — 아직 저장하지 않았습니다",
    "en": "Preview — nothing has been saved"
  },
  "traceImport.added": {
    "ko": "추가",
    "en": "Added"
  },
  "traceImport.deleted": {
    "ko": "삭제",
    "en": "Deleted"
  },
  "traceImport.issues": {
    "ko": "반영할 수 없는 문제",
    "en": "Issues preventing application"
  },
  "traceImport.untouched": {
    "ko": "CSV에 담기지 않아 이 들여오기가 건드리지 않는 것",
    "en": "Not included in CSV and left untouched by this import"
  },
  "traceImport.noChange": {
    "ko": "바뀌는 것이 없습니다.",
    "en": "There are no changes."
  },
  "traceImport.recompute": {
    "ko": "반영하면 바뀐 입력에 해당하는 판정이 같은 순간에 다시 계산됩니다. 이전 판정은 이력에 남습니다.",
    "en": "Applying changes recomputes affected assessments atomically. Previous assessments remain in history."
  },
  "traceImport.apply": {
    "ko": "이 내용으로 반영",
    "en": "Apply these changes"
  },
  "traceEvidence.notice": {
    "ko": "근거 위치는 이 판정에 연결된 자료의 위치 기록입니다. 연결되어 있다는 것만으로 THOTH가 그 문장을 근거로 확인한 것은 아닙니다.",
    "en": "Evidence locations identify positions in sources linked to this assessment. A link alone does not mean THOTH verified the passage as supporting evidence."
  },
  "traceEvidence.loading": {
    "ko": "자료에서 위치를 찾는 중…",
    "en": "Locating the passage in the sources…"
  },
  "traceEvidence.failed": {
    "ko": "자료 목록을 읽지 못해 이 위치를 열 수 없습니다. 위치가 없다는 뜻은 아닙니다.",
    "en": "The source list could not be read, so this location cannot be opened. This does not mean the location is absent."
  },
  "traceEvidence.notFound": {
    "ko": "이 위치를 이 프로젝트에 연결된 자료에서 찾지 못했습니다. 자료를 연결하지 않았거나, 위치 표기가 THOTH가 만든 원문 위치가 아닐 수 있습니다.",
    "en": "This location was not found in the project's connected sources. The source may not be connected, or the locator may not have been created by THOTH."
  },
  "traceEvidence.passage": {
    "ko": "이 위치의 문장 (강조 표시)",
    "en": "Passage at this location (highlighted)"
  },
  "traceConfirm.reasonNeeded": {
    "ko": "확인한 이유를 적어 주세요.",
    "en": "Enter the reason for confirmation."
  },
  "traceConfirm.title": {
    "ko": "판정 확인 기록",
    "en": "Record assessment confirmation"
  },
  "traceConfirm.notice": {
    "ko": "확인은 사람이 이 판정을 보았다는 기록만 남깁니다. 판정은 바뀌지 않고, 나중에 다시 계산되면 새 판정에는 이 확인이 옮겨지지 않습니다.",
    "en": "Confirmation records that a person reviewed this assessment. It does not change the assessment or carry over to a later recomputation."
  },
  "traceConfirm.reason": {
    "ko": "확인한 이유",
    "en": "Reason for confirmation"
  },
  "traceConfirm.record": {
    "ko": "확인 기록 남기기",
    "en": "Record confirmation"
  },
  "traceHistory.all": {
    "ko": "모든 결과가 기준을 만족해야 함",
    "en": "Every result must meet the criterion"
  },
  "traceHistory.latest": {
    "ko": "조건별로 가장 최근 결과",
    "en": "Latest result for each condition"
  },
  "traceHistory.current": {
    "ko": "현재 판정",
    "en": "Current assessment"
  },
  "traceHistory.previous": {
    "ko": "이전 판정",
    "en": "Previous assessment"
  },
  "traceHistory.initial": {
    "ko": "처음 계산한 판정입니다.",
    "en": "This is the first computed assessment."
  },
  "traceHistory.moreNotice": {
    "ko": "이 앞의 판정은 \"이전 이력 더 보기\"로 볼 수 있습니다.",
    "en": "Use Load earlier history to view preceding assessments."
  },
  "traceHistory.trigger": {
    "ko": "계산을 일으킨 일",
    "en": "What triggered the calculation"
  },
  "traceHistory.noChanges": {
    "ko": "이 판정에 영향을 준 입력 변경은 기록되지 않았습니다.",
    "en": "No input changes affecting this assessment were recorded."
  },
  "traceHistory.noResult": {
    "ko": "고를 수 있는 결과가 없었습니다.",
    "en": "No result was eligible for selection."
  },
  "traceHistory.requiredCriteria": {
    "ko": "판정에 쓴 필수 기준",
    "en": "Required criteria used in the assessment"
  },
  "traceHistory.reason": {
    "ko": "이유",
    "en": "Reason"
  },
  "traceHistory.noConfirmation": {
    "ko": "이 판정에 대한 확인은 없습니다.",
    "en": "No confirmation is recorded for this assessment."
  },
  "traceHistory.revision": {
    "ko": "판정 개정",
    "en": "Assessment revision"
  },
  "traceHistory.previousRevision": {
    "ko": "이전 개정",
    "en": "Previous revision"
  },
  "traceHistory.none": {
    "ko": "아직 계산된 판정이 없습니다.",
    "en": "No assessment has been calculated yet."
  },
  "traceHistory.title": {
    "ko": "판정 이력",
    "en": "Assessment history"
  },
  "traceHistory.failed": {
    "ko": "이전 이력을 읽지 못했습니다. 이력이 없다는 뜻이 아닙니다.",
    "en": "Earlier history could not be read. This does not mean no history exists."
  },
  "traceHistory.more": {
    "ko": "이전 이력 더 보기",
    "en": "Load earlier history"
  },
  "recheck.reasonNeeded": {
    "ko": "이유를 하나 고르세요.",
    "en": "Choose a reason."
  },
  "recheck.writtenNeeded": {
    "ko": "이 이유는 글로 적어야 합니다.",
    "en": "This reason requires a written explanation."
  },
  "recheck.notice": {
    "ko": "사람이 바뀐 판정을 보고 이 가설을 어떻게 볼지 적어 두는 기록입니다. 가설도 판정도 바뀌지 않고, 누가 언제 왜 확인했는지만 남습니다. 기록은 지워지지 않습니다.",
    "en": "Records a person's view of this hypothesis after an assessment changed. The hypothesis and assessment are unchanged; who reviewed it, when, and why are retained."
  },
  "recheck.basisOnly": {
    "ko": "판정 상태는 그대로이고, 판정이 기댄 결과나 근거 위치만 바뀌었습니다. 바뀐 근거를 직접 본 뒤 이유를 고르세요.",
    "en": "The assessment state is unchanged; only its results or evidence locations changed. Review the changed evidence before choosing a reason."
  },
  "recheck.writtenLabel": {
    "ko": "이유를 글로 적어 주세요 (필수)",
    "en": "Written reason (required)"
  },
  "recheck.optionalNote": {
    "ko": "메모 (선택)",
    "en": "Note (optional)"
  },
  "recheck.writeReason": {
    "ko": "이유를 글로 적어 주세요",
    "en": "Write the reason"
  },
  "recheck.note": {
    "ko": "메모",
    "en": "Note"
  },
  "recheck.flipped": {
    "ko": "충족과 미달이 뒤바뀐 변경이라, 가설을 그대로 두려면 이유를 글로 적어야 합니다.",
    "en": "The assessment switched between met and unmet. Keeping the hypothesis requires a written explanation."
  },
  "testResult.choose": {
    "ko": "어느 설명과 맞았는지 고르세요.",
    "en": "Choose which explanation matched the result."
  },
  "testResult.observationNeeded": {
    "ko": "관찰한 내용을 적어 주세요.",
    "en": "Enter your observation."
  },
  "testResult.rerecord": {
    "ko": "결과 다시 기록",
    "en": "Record another result"
  },
  "testResult.record": {
    "ko": "결과 기록",
    "en": "Record result"
  },
  "testResult.title": {
    "ko": "시험 결과 기록",
    "en": "Record test result"
  },
  "testResult.notice": {
    "ko": "사람이 한 시험의 결과를 적어 둡니다. 가설은 바뀌지 않고, 기록은 지워지지 않습니다. 같은 시험을 다시 기록하면 가장 최근 기록이 반영됩니다.",
    "en": "Records a test performed by a person. The hypothesis and previous records are retained. Recording the same test again makes the latest record active."
  },
  "testResult.match": {
    "ko": "어느 설명과 맞았습니까",
    "en": "Which explanation matched?"
  },
  "testResult.requiredObservation": {
    "ko": "관찰한 내용 (필수)",
    "en": "Observation (required)"
  },
  "testResult.observation": {
    "ko": "관찰한 내용",
    "en": "Observation"
  },
  "testResult.optionalRefs": {
    "ko": "근거 참조 (선택, 한 줄에 하나)",
    "en": "Evidence references (optional, one per line)"
  },
  "testResult.refs": {
    "ko": "근거 참조",
    "en": "Evidence references"
  },
  "testResult.failed": {
    "ko": "기록하지 못했습니다. 잠시 뒤 다시 시도해 주세요.",
    "en": "The record could not be saved. Try again shortly."
  },
  "testResult.save": {
    "ko": "기록 남기기",
    "en": "Save record"
  },
  "closure.choose": {
    "ko": "처분 종류를 고르세요.",
    "en": "Choose a disposition type."
  },
  "closure.basisNeeded": {
    "ko": "근거 문서를 적어 주세요.",
    "en": "Enter a supporting document."
  },
  "closure.scopeNeeded": {
    "ko": "해당 조건 범위를 적어 주세요.",
    "en": "Enter the applicable condition scope."
  },
  "closure.kind": {
    "ko": "처분 종류",
    "en": "Disposition type"
  },
  "closure.requiredBasis": {
    "ko": "근거 문서 (필수)",
    "en": "Supporting document (required)"
  },
  "closure.basis": {
    "ko": "근거 문서",
    "en": "Supporting document"
  },
  "closure.basisExample": {
    "ko": "문서 번호, 회의록, 변경 요청서 등",
    "en": "Document number, meeting minutes, change request, etc."
  },
  "closure.requiredScope": {
    "ko": "해당 조건 범위 (필수)",
    "en": "Applicable condition scope (required)"
  },
  "closure.scope": {
    "ko": "해당 조건 범위",
    "en": "Applicable condition scope"
  },
  "closure.failed": {
    "ko": "기록하지 못했습니다. 판정이 그 사이 바뀌었을 수 있으니 화면을 새로 읽은 뒤 다시 시도해 주세요.",
    "en": "The record could not be saved. The assessment may have changed; refresh the view before trying again."
  },
  "refutation.conditions": {
    "ko": "기각 조건",
    "en": "Refutation conditions"
  },
  "refutation.empty": {
    "ko": "아직 적어 둔 기각 조건이 없습니다.",
    "en": "No refutation conditions have been recorded yet."
  },
  "refutation.record": {
    "ko": "기각 조건 기록",
    "en": "Record refutation conditions"
  },
  "refutation.notice": {
    "ko": "이런 결과가 나오면 이 가설이 틀렸다고 볼 조건을 한 줄에 하나씩 적습니다. 새로 기록하면 가장 최근 목록이 보이고, 이전 기록은 남습니다.",
    "en": "Enter one condition per line describing a result that would refute this hypothesis. New records become the displayed list; earlier records are retained."
  },
  "memoryEdit.required": {
    "ko": "고친 내용과 이유를 모두 적어 주세요.",
    "en": "Enter both the corrected content and the reason."
  },
  "memoryEdit.title": {
    "ko": "기억 수정 제안",
    "en": "Propose a memory correction"
  },
  "memoryEdit.notice": {
    "ko": "제안은 검토를 거쳐 반영되면 새 버전이 됩니다. 원래 기억은 지워지지 않고 이력에 남습니다.",
    "en": "If accepted after review, the proposal becomes a new version. The original memory remains in history."
  },
  "memoryEdit.content": {
    "ko": "고친 내용",
    "en": "Corrected content"
  },
  "memoryEdit.send": {
    "ko": "수정 제안 보내기",
    "en": "Submit correction proposal"
  }
} as const;
