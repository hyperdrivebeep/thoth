export const traceMessages = {
  "trace.decider.test": {
    "ko": "시험 책임자",
    "en": "Test owner"
  },
  "trace.decider.validity": {
    "ko": "지정 검증자",
    "en": "Designated verifier"
  },
  "trace.decider.rule": {
    "ko": "요구사항 담당자",
    "en": "Requirements owner"
  },
  "trace.decider.disposition": {
    "ko": "해당 권한자",
    "en": "Authorized decision maker"
  },
  "trace.next.stale": {
    "ko": "바뀐 근거로 판정을 다시 볼지 확인합니다. 이전 안내는 이전 근거 기준입니다.",
    "en": "Check whether the assessment needs review against the changed evidence. Earlier guidance used the earlier basis."
  },
  "trace.next.investigate": {
    "ko": "시험이 유효했는지, 다른 원인 후보가 있는지 확인합니다.",
    "en": "Check whether the test was valid and whether alternative causes exist."
  },
  "trace.next.noResult": {
    "ko": "이 조건의 시험 결과가 있는지, 있다면 추적표에 연결됐는지 확인합니다. 없으면 추가 시험 계획을 검토합니다.",
    "en": "Check whether this condition has a test result and whether it is linked to the trace table. If no result exists, review plans for additional testing."
  },
  "trace.next.invalid": {
    "ko": "결과의 ID·단위·분모·시험 조건을 원자료와 대조하고, 그 결과를 쓸지 정합니다.",
    "en": "Compare the result ID, units, denominator, and test conditions with the original sources, then decide whether the result can be used."
  },
  "trace.next.rule": {
    "ko": "요구사항 원문에서 승인된 판정 규칙과 그 버전을 확인해 규칙을 확정합니다.",
    "en": "Confirm the approved assessment rule and its version in the original requirements."
  },
  "trace.next.untested": {
    "ko": "이 기준에 연결된 시험의 결과가 아직 들어오지 않았습니다. 시험이 진행됐는지 확인하고, 결과가 없으면 시험 계획을 검토합니다.",
    "en": "The linked test has no result yet. Check whether it ran; if no result exists, review the test plan."
  },
  "trace.next.unlinked": {
    "ko": "이 기준을 확인할 시험이 연결돼 있지 않습니다. 이 기준을 시험으로 확인해야 하는지 정하고, 필요하면 시험과 연결합니다.",
    "en": "No test is linked to this criterion. Decide whether testing is needed and link a test if appropriate."
  },
  "trace.next.condition": {
    "ko": "결과는 있지만 기준의 조건과 달라 쓰지 않았습니다. 기준의 조건이 맞는지, 이 조건으로 시험이 더 필요한지 확인합니다.",
    "en": "A result exists but was not used because its conditions differ. Check the criterion conditions and whether another test is needed."
  },
  "trace.next.unit": {
    "ko": "결과의 단위가 기준의 단위와 다릅니다. 원자료와 대조해 어느 쪽 단위가 맞는지 확인하고, 그 결과를 쓸지 정합니다.",
    "en": "The result and criterion units differ. Check the original sources to determine the correct units and whether the result can be used."
  },
  "trace.next.denominator": {
    "ko": "결과의 분모가 0 이하이거나, 값이 분자·분모로 계산한 값과 다릅니다. 분자·분모와 값을 원자료와 대조해 어느 쪽이 틀렸는지 확인합니다.",
    "en": "The denominator is nonpositive or the value differs from the numerator/denominator calculation. Compare all three with the original sources to find the error."
  },
  "trace.next.value": {
    "ko": "결과의 값이 비었거나 숫자가 아니거나 너무 큽니다. 원자료에서 값을 다시 확인해 결과를 고칠지 정합니다.",
    "en": "The result is missing, nonnumeric, or too large. Check the original value and decide whether to correct the result."
  },
  "trace.next.additionalTests": {
    "ko": "아직 시험하지 않은 조건은 시험 계획을 따로 세웁니다. 미달 조사가 끝나도 이 계획은 남습니다.",
    "en": "Plan tests separately for untested conditions. This work remains even after investigating the failed criterion."
  },
  "trace.next.followRow": {
    "ko": "결과가 없는 조건은 각 기준 줄의 '다음 확인'을 따릅니다.",
    "en": "For conditions without results, follow the next checks on each criterion row."
  },
  "trace.next.required": {
    "ko": "이 요구사항에 필수 기준이 정의돼 있는지 확인합니다.",
    "en": "Check whether required criteria are defined for this requirement."
  },
  "trace.trigger.initial": {
    "ko": "처음 계산",
    "en": "Initial calculation"
  },
  "trace.trigger.import": {
    "ko": "CSV 들여오기 반영",
    "en": "CSV import applied"
  },
  "trace.trigger.recompute": {
    "ko": "다시 계산",
    "en": "Recomputed"
  },
  "trace.trigger.other": {
    "ko": "다른 이유로 다시 계산",
    "en": "Recomputed for another reason"
  },
  "trace.actor.system": {
    "ko": "THOTH 계산",
    "en": "THOTH calculation"
  },
  "trace.actor.local": {
    "ko": "이 컴퓨터의 사용자",
    "en": "User on this computer"
  },
  "trace.field.value": {
    "ko": "측정값",
    "en": "Measured value"
  },
  "trace.field.unit": {
    "ko": "단위",
    "en": "Unit"
  },
  "trace.field.condition": {
    "ko": "조건",
    "en": "Condition"
  },
  "trace.field.numerator": {
    "ko": "분자",
    "en": "Numerator"
  },
  "trace.field.denominator": {
    "ko": "분모",
    "en": "Denominator"
  },
  "trace.field.resultRevision": {
    "ko": "결과 개정",
    "en": "Result revision"
  },
  "trace.field.sourceRefs": {
    "ko": "근거 위치",
    "en": "Evidence locations"
  },
  "trace.field.rawValue": {
    "ko": "원문 값",
    "en": "Original value"
  },
  "trace.field.observedAt": {
    "ko": "관측 시각",
    "en": "Observation time"
  },
  "trace.field.criterion": {
    "ko": "대상 기준",
    "en": "Target criterion"
  },
  "trace.field.threshold": {
    "ko": "기준값",
    "en": "Threshold"
  },
  "trace.field.comparator": {
    "ko": "비교 방향",
    "en": "Comparison direction"
  },
  "trace.field.required": {
    "ko": "필수 여부",
    "en": "Required status"
  },
  "trace.field.rounding": {
    "ko": "반올림",
    "en": "Rounding"
  },
  "trace.field.measure": {
    "ko": "측정 항목",
    "en": "Measure"
  },
  "trace.field.ruleRevision": {
    "ko": "규칙 개정",
    "en": "Rule revision"
  },
  "trace.field.policy": {
    "ko": "결과 선택 정책",
    "en": "Result selection policy"
  },
  "trace.field.empty": {
    "ko": "비어 있음",
    "en": "Empty"
  },
  "trace.field.content": {
    "ko": "내용",
    "en": "Content"
  },
  "trace.condition.weather": {
    "ko": "날씨",
    "en": "Weather"
  },
  "trace.condition.dry": {
    "ko": "맑음(건조)",
    "en": "Clear (dry)"
  },
  "trace.condition.rain": {
    "ko": "비",
    "en": "Rain"
  },
  "trace.condition.fog": {
    "ko": "안개",
    "en": "Fog"
  },
  "trace.change.ruleRemoved": {
    "ko": "판정 규칙 삭제",
    "en": "Assessment rule removed"
  },
  "trace.change.ruleAdded": {
    "ko": "판정 규칙 추가",
    "en": "Assessment rule added"
  },
  "trace.change.policy": {
    "ko": "결과 선택 정책이 바뀌었습니다.",
    "en": "The result selection policy changed."
  },
  "trace.change.initial": {
    "ko": "새로 계산됨",
    "en": "Newly calculated"
  },
  "trace.result.superseded": {
    "ko": "더 최근 결과가 있어 제외",
    "en": "Excluded because a newer result exists"
  },
  "trace.result.condition": {
    "ko": "조건이 달라 제외",
    "en": "Excluded because conditions differ"
  },
  "trace.result.excluded": {
    "ko": "제외",
    "en": "Excluded"
  },
  "trace.loss.evidence": {
    "ko": "근거 문장 자체(위치만 담깁니다)",
    "en": "Evidence text itself (only locations are included)"
  },
  "trace.loss.history": {
    "ko": "판정 이력(들여온 뒤 다시 계산됩니다)",
    "en": "Assessment history (recomputed after import)"
  },
  "trace.loss.confirmation": {
    "ko": "누가 어떤 판정을 확인했는지",
    "en": "Who confirmed each assessment"
  },
  "trace.loss.policy": {
    "ko": "결과를 고르는 정책",
    "en": "Result selection policy"
  },
  "trace.loss.pending": {
    "ko": "다시 계산이 필요한 항목 표시",
    "en": "Markers for items requiring recomputation"
  },
  "trace.loss.closures": {
    "ko": "처분 표시 열(읽기 전용이라 고쳐도 반영되지 않습니다. 처분은 THOTH에서 기록하세요)",
    "en": "Disposition display column (read-only; edits are not applied. Record dispositions in THOTH.)"
  },
  "trace.import.stale": {
    "ko": "이 파일은 지금의 추적표와 다른 상태에서 내보낸 것입니다. 그 뒤에 추적표가 바뀌었습니다.",
    "en": "This file was exported from a different state. The trace table has changed since then."
  },
  "trace.import.reexport": {
    "ko": "지금 상태에서 'CSV로 내보내기'를 다시 한 뒤, 그 파일을 고쳐서 들여오세요.",
    "en": "Export the current table as CSV, edit that file, then import it."
  },
  "trace.import.noBase": {
    "ko": "파일에 어느 상태에서 내보낸 것인지 표시가 없습니다.",
    "en": "The file does not identify the state it was exported from."
  },
  "trace.import.keepHeaders": {
    "ko": "'CSV로 내보내기'로 받은 파일을 그대로 고쳐 쓰세요. 머리 줄과 기준 상태 열은 지우지 마세요.",
    "en": "Edit the exported CSV. Keep its header and base-state columns."
  },
  "trace.import.mixedBase": {
    "ko": "한 파일 안에 서로 다른 상태에서 내보낸 줄이 섞여 있습니다.",
    "en": "The file mixes rows exported from different states."
  },
  "trace.import.oneFile": {
    "ko": "한 번에 내보낸 파일 하나만 고치세요.",
    "en": "Edit a single file from one export."
  },
  "trace.import.exists": {
    "ko": "이 프로젝트에는 이미 추적표가 있어 '새로 만들기'를 할 수 없습니다.",
    "en": "This project already has a trace table, so a new one cannot be created."
  },
  "trace.import.chooseUpdate": {
    "ko": "'기존 표 고치기'를 선택하세요.",
    "en": "Choose Update existing table."
  },
  "trace.import.noTable": {
    "ko": "고칠 추적표가 아직 없습니다.",
    "en": "There is no trace table to update."
  },
  "trace.import.chooseCreate": {
    "ko": "'새로 만들기'를 선택하세요.",
    "en": "Choose Create new table."
  },
  "trace.import.deduplicate": {
    "ko": "중복된 줄을 하나로 정리하세요.",
    "en": "Remove duplicate rows."
  },
  "trace.import.validFields": {
    "ko": "숫자 칸에는 숫자만, 종류 칸에는 정해진 값만 쓰세요.",
    "en": "Use numbers in numeric fields and allowed values in type fields."
  },
  "trace.import.keepKey": {
    "ko": "내부 키 칸은 내보낸 값 그대로 두세요.",
    "en": "Keep exported internal keys unchanged."
  },
  "trace.import.checkDelete": {
    "ko": "삭제 줄의 종류와 ID를 확인하세요.",
    "en": "Check the type and ID in the deletion row."
  },
  "trace.import.deleteLinks": {
    "ko": "연결 줄의 삭제 줄도 함께 넣거나, 항목 삭제를 빼세요.",
    "en": "Include deletion of the linked rows, or remove the item deletion."
  },
  "trace.import.deleteRules": {
    "ko": "규칙의 삭제 줄도 함께 넣거나, 기준 삭제를 빼세요.",
    "en": "Include deletion of the rule, or remove the criterion deletion."
  },
  "trace.import.deleteResults": {
    "ko": "결과의 삭제 줄도 함께 넣거나, 삭제를 빼세요.",
    "en": "Include deletion of the result, or remove the deletion."
  },
  "trace.import.chooseOne": {
    "ko": "둘 중 하나만 남기세요.",
    "en": "Keep only one of the two actions."
  },
  "trace.import.noDeleteCreate": {
    "ko": "'새로 만들기'에는 삭제 줄을 쓸 수 없습니다.",
    "en": "Deletion rows are not allowed when creating a new table."
  },
  "trace.import.removeDelete": {
    "ko": "삭제 줄을 빼거나 '기존 표 고치기'를 선택하세요.",
    "en": "Remove deletion rows or choose Update existing table."
  },
  "trace.import.deleteIdentity": {
    "ko": "삭제 줄에는 종류와 ID를 모두 쓰세요.",
    "en": "Supply both type and ID for each deletion."
  },
  "trace.import.headerMissing": {
    "ko": "파일의 머리 줄에 꼭 필요한 열이 빠져 있습니다.",
    "en": "Required columns are missing from the header."
  },
  "trace.import.useExportHeader": {
    "ko": "'CSV로 내보내기'로 받은 파일의 머리 줄을 지우지 말고 쓰세요.",
    "en": "Keep the header from the exported CSV file."
  },
  "trace.import.headerDuplicate": {
    "ko": "머리 줄에 같은 열 이름이 두 번 있습니다.",
    "en": "The header contains a duplicate column name."
  },
  "trace.import.removeColumn": {
    "ko": "중복된 열을 지우세요.",
    "en": "Remove duplicate columns."
  },
  "trace.import.useExport": {
    "ko": "'CSV로 내보내기'로 받은 파일을 쓰세요.",
    "en": "Use a file obtained from Export CSV."
  },
  "trace.import.allowedTypes": {
    "ko": "줄의 종류는 항목·연결·규칙·결과·삭제 중 하나여야 합니다.",
    "en": "Row types must be item, link, rule, result, or deletion."
  },
  "trace.import.fillId": {
    "ko": "ID 칸을 채우거나 그 줄을 지우세요.",
    "en": "Fill in the ID or remove the row."
  },
  "trace.import.empty": {
    "ko": "파일이 비어 있습니다.",
    "en": "The file is empty."
  },
  "trace.import.chooseNonempty": {
    "ko": "내용이 있는 파일을 고르세요.",
    "en": "Choose a file containing data."
  },
  "trace.import.noRows": {
    "ko": "파일에 들여올 줄이 없습니다.",
    "en": "The file has no rows to import."
  },
  "trace.import.tooLarge": {
    "ko": "파일이 너무 큽니다.",
    "en": "The file is too large."
  },
  "trace.import.split": {
    "ko": "파일을 나누어 들여오세요.",
    "en": "Split the file before importing."
  },
  "trace.import.tooManyRows": {
    "ko": "줄이 너무 많습니다.",
    "en": "There are too many rows."
  },
  "trace.import.unreadable": {
    "ko": "CSV로 읽을 수 없는 파일입니다.",
    "en": "The file could not be read as CSV."
  },
  "trace.import.utf8": {
    "ko": "UTF-8 CSV 파일인지 확인하세요.",
    "en": "Check that the file is UTF-8 CSV."
  },
  "trace.import.problem": {
    "ko": "이 파일을 반영할 수 없는 문제가 있습니다.",
    "en": "A problem prevents applying this file."
  },
  "trace.import.checkDetails": {
    "ko": "기술 정보를 확인한 뒤 파일을 고쳐 다시 시도하세요.",
    "en": "Check the technical details, correct the file, and try again."
  },
  "trace.apply.stale": {
    "ko": "미리보기를 본 뒤에 추적표가 바뀌어 반영하지 않았습니다.",
    "en": "The trace table changed after preview, so nothing was applied."
  },
  "trace.apply.previewAgain": {
    "ko": "파일을 다시 선택해 미리보기부터 다시 하세요.",
    "en": "Select the file again and restart from preview."
  },
  "trace.apply.fileChanged": {
    "ko": "미리보기에서 본 파일과 반영하려는 파일이 달라 반영하지 않았습니다.",
    "en": "The file differs from the previewed file, so nothing was applied."
  },
  "trace.apply.unresolved": {
    "ko": "파일에 해결되지 않은 문제가 있어 반영하지 않았습니다.",
    "en": "The file still has unresolved issues, so nothing was applied."
  },
  "trace.apply.fixPreview": {
    "ko": "미리보기의 문제를 고친 파일로 다시 시도하세요.",
    "en": "Correct the preview issues and try again."
  },
  "trace.apply.conflict": {
    "ko": "다른 곳에서 먼저 추적표가 바뀌어 반영하지 않았습니다.",
    "en": "The trace table was changed elsewhere, so nothing was applied."
  },
  "trace.apply.reload": {
    "ko": "화면을 새로 읽은 뒤 다시 시도하세요.",
    "en": "Refresh the view and try again."
  },
  "trace.apply.closed": {
    "ko": "닫는 중이거나 보관된 프로젝트에서는 추적표를 바꿀 수 없습니다.",
    "en": "The trace table cannot be changed in a closing or archived project."
  },
  "trace.apply.checkProject": {
    "ko": "프로젝트 상태를 확인하세요.",
    "en": "Check the project state."
  },
  "trace.apply.failed": {
    "ko": "반영하지 못했습니다. 아무것도 저장되지 않았습니다.",
    "en": "The changes could not be applied. Nothing was saved."
  },
  "trace.apply.retry": {
    "ko": "잠시 뒤 다시 시도하세요.",
    "en": "Try again shortly."
  }
} as const;
