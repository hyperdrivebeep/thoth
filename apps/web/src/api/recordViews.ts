export type RecordView = { namespace: string; label: string; description: string; method: string | null; scope?: "thread" | "operation"; note?: string };
/** Dedicated read entrypoints; backend command schemas remain the authority. */
export const recordViews: RecordView[] = [
  {namespace:"project",label:"프로젝트",description:"기준시점 · 정책 결속 · 생명주기",method:"project/read"},
  {namespace:"thread",label:"작업",description:"입력 반영 · 조사 진행 · 현재 결과",method:"thread/read",scope:"thread"},
  {namespace:"investigation",label:"탐색",description:"탐색 범위 · 수집 단계 · 중단 사유",method:"investigation/list"},
  {namespace:"evidence",label:"근거",description:"원문 위치 · 지지 · 권위 · 시점",method:"evidence/list"},
  {namespace:"object",label:"판단 객체",description:"질문에 결속된 판단과 주의 항목",method:"object/list"},
  {namespace:"criteria",label:"평가기준",description:"공식 기준과 참고 후보의 분리",method:"criteria/list"},
  {namespace:"hypothesis",label:"가설",description:"경쟁 설명 · 반증 · 예측 · 평가",method:"hypothesis/list"},
  {namespace:"action",label:"행동",description:"대안 · 계획 · 위험 · 실행 권한",method:"action/list"},
  {namespace:"execution",label:"실행 · 복구",description:"시도 · 효과 · 재조정 · 취소 확인",method:"execution/list"},
  {namespace:"outcome",label:"관찰 결과",description:"시험 유효성 · 관찰 · 해석의 한계",method:"outcome/list"},
  {namespace:"revision",label:"변경 · 복원",description:"불변 revision · 분기 · 작업 head",method:"revision/list"},
  {namespace:"memory",label:"프로젝트 기억",description:"저장 · recall · 행동 자격의 분리",method:"memory/list"},
  {namespace:"improvement",label:"개선",description:"후보 · 평가 · 노출 · 승격 · rollback",method:"improvement/list"},
  {namespace:"receipt",label:"영수증",description:"무결성 · 출처 · 주장 범위 · lineage",method:"receipt/list"},
  {namespace:"closure",label:"마감",description:"준비도 · 처분 · 열린 항목 · 보존",method:"closure/list"},
  {namespace:"export",label:"내보내기",description:"목적 · audience · 고정 snapshot · 공개 경계",method:"export/list"},
  {namespace:"operation",label:"비동기 제어",description:"실행 상태 · checkpoint · 복구 정보",method:"operation/read",scope:"operation"},
  {namespace:"model",label:"모델 · 설정",description:"기본값 · 추론강도 · 실제 소비 snapshot",method:"model/settings/read"},
  {namespace:"workspace",label:"워크스페이스 설정",description:"모델 연결 · 인터넷 동의 · 첫 실행 준비",method:"workspace/setup/read"},
  {namespace:"projectpack",label:"검증 예제",description:"등록된 ProjectPack · scripted/live 구분",method:"projectpack/list",note:"예제 조회는 실행하지 않습니다. 실제 연구 기본 흐름과 별도입니다."},
  {namespace:"field",label:"현장 평가",description:"프로토콜 · 참여 세션 · 점수 · privacy-safe export",method:null,note:"현재 공개 field namespace는 COMMAND만 제공합니다. 비변경 조회 API는 없으며 이 UI는 실제 참가자 평가나 시간절감·WTP를 입증하지 않습니다. 승인된 프로토콜·참여 동의가 있는 경우에만 고급 명령을 사용하세요."},
];

export const visibleRecordViews = recordViews.filter(
  (view) => view.namespace !== "projectpack" && view.namespace !== "field",
);
