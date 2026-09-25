# THOTH repository guidance

적용일: 2026-09-13. 사용자가 검토한 `agent-guidance-proposal.md`의 본문을 채택했다.

## 적용 범위와 기존 문서

- 이 파일은 THOTH 개발 지침이다. 비활성 backup AGENTS는 역사적 비교 자료이며 현재 지침으로 복원하지 않는다.
- 사용자 선택에 따라 개발 Hook은 비활성 상태를 유지한다. 이 MD의 적용이 Hook 신뢰·실행이나 과거 자동 preflight/Stop 절차의 재활성화를 뜻하지 않는다.
- 상세 문서의 제품 runtime·권한·상태·데이터·OCP 계약은 유지한다. `agent-execution-rules.md`의 Development Hook ownership 절은 해당 Hook 운용 모드의 기록이다. 이를 근거로 꺼 둔 Hook을 자동 복원하지 않는다.
- 현재 세션의 상위 필수 지침과 유효한 검증 정책은 준수한다. Acceptance/preflight가 요구되는 상황에서 이를 위조·우회하지 않는다. 비활성 환경과 필수 절차가 충돌하면 정확한 조건을 보고하고 허용된 진단·계획 작업만 계속한다.
- 개발 중 집중 검사, 통합 마감 시 정상 진입 검증, 전체 공통 기반 수정의 최종 동결에서 단일 전체 제품 회귀를 목표로 한다. 기존 FULL/TOOLING 검사기를 조용히 바꾸거나 실패를 PASS로 재분류하지 않는다.
- 2026-09-14 사용자 지시로 비활성 개발 Hook에만 의존하는 명시 목록은 현재 환경의 기본 검사에서 제외한다. 제품·구조 검사는 유지하고 제외 수를 보고하며, Hook 재활성화 또는 명시 진단 실행에서는 해당 검사도 포함한다.

## 작업 범위

- 조사·설명·설계 요청은 제품 수정을 허가하지 않는다. 구현 요청에서는 합의한 범위를 끝까지 수행하되, 의미 변경·외부 권한·비용이 필요한 부분만 질문한다.
- 현재 작업서는 해당 인수조건의 기준이다. PROJECT_WIKI/NOW.md에서 최신 작업을 확인하고, 아래 상세 계약은 변경 영역에 해당하는 것만 읽는다. 과거 완료 보고·기억보다 현재 코드와 해당 source의 실행 증거를 우선한다.
- 기존 dirty 변경과 사용자 데이터를 보존한다. 검사 실패를 숨기려고 테스트·정책·기준값·예외 허용을 바꾸지 않는다. 변경이 필요하면 이유와 새 계약을 명시한다.

## 제품을 보존할 것

- 연결 자료 우선 탐색, 근거·반증 심사, 가설·참고값·다음 행동, 기존 Outcome·Project Memory·revision/restore·receipt 연결을 유지한다. API 이름만 남기고 실제 소비 경로를 빼지 않는다.
- AI 제안, 코드 검증, 인간의 공식 권한을 구분한다. 부족한 정보는 영향을 받는 판단·행동만 제한하고 허용된 조사는 계속한다. 필수 조건을 만들어내거나 조용히 완화하지 않는다.
- 근거 부족·미평가·0/1개 가설은 유효한 부분 상태다. 가짜 후보·점수·완료 상태로 채우지 않는다. 공식 KPI/안전 임계값/waiver/최종 처분에는 기존 권한 계약을 따른다.

## 코드 구조와 확장

- domain은 typed 데이터/불변식, ports는 추상 계약, application은 use case와 상태 전이, adapters는 I/O, apps는 조립을 맡는다. application에서 concrete adapter를 import하거나 생성하지 않는다.
- 기존 Model/Connector/Sandbox/Parser/store/Profile의 새 종류는 registry/factory로 추가한다. 제품 core에 provider/프로젝트/Hero/정답 이름 분기를 추가하지 않는다.
- 새 변형 하나를 등록했을 때 기존 application 분기 수정 없이 정상 소비되는지 확인한다. 계약 자체가 달라져 core 변경이 필요하면 그 변경을 숨기지 말고 이유와 compatibility를 기록한다.
- 파일/함수는 책임으로 나눈다. 기존 거대 함수에 새 orchestration 책임을 계속 더하지 않는다. 분리를 위한 새 추상화는 실제 호출자와 독립 검사로 설명할 수 있어야 한다.

## 저장·상태·현재성

- 연구 객체의 정본은 하나다. 파생 view/index/summary를 별도 사실 정본으로 만들지 않는다. 새 canonical payload에는 typed codec과 version 검증을 둔다.
- 한 checkpoint의 record/head/current manifest/input 적용/event/receipt는 짧은 한 UoW로 공개되거나 모두 이전 상태여야 한다. 모델·네트워크 I/O는 DB transaction 밖에서 실행한다.
- 접수, 대기, 실제 적용, 부분 결과, pause, 완료를 구분한다. queue 응답을 완료 결과로 취급하지 않는다. 같은 key/payload 재전송은 원 작업을 손상시키지 않는다.
- 재전송/복구의 거부는 시도한 caller에게 귀속하고 원 작업을 변경하지 않는다. 공유 읽기와 실행 재진입 권한을 구분한다.
- 입력·정책·시점·자료·권한의 현재 basis를 호출과 게시에서 확인하고, 이후 변경된 결과는 해당 사용 범위에서 stale로 표시한다. 과거 기록을 지우거나 새 current 결과로 위장하지 않는다.
- 결과·설정·근거의 표시값과 실제 소비값을 같은 basis에서 만들고, 중복 상태 소유를 피한다.

## 경계와 기존 기능의 소비

- 새 경로는 기존 policy/snapshot/use, resource scope, full identity, budget을 실제로 소비해야 한다. wrapper가 존재한다는 사실만으로 통합됐다고 보지 않는다.
- schema는 모델이 내는 객체뿐 아니라 실제 provider 직렬화물과 소비자의 입력 계약까지 확인한다. guidance/tool/schema/repair를 포함한 최종 전송물과 누적 예산을 일치시킨다.
- 모델/Connector의 각 I/O와 cleanup에는 기한이 있어야 한다. cancel 요청과 실제 종료 확인을 구분한다. 재시작으로 예산이나 실행 권한을 새로 만들지 않는다.
- 외부 효과 후 후처리 실패는 별도로 재개하며 이미 수행된 효과를 반복하지 않는다. 남은 후속 항목은 처분·책임·trigger·잔여 위험을 함께 기록한다.
- 미지원 route를 fake 성공으로 바꾸지 않는다. 지원 가능한 기존 경로는 조사하고, 새 계정·비용·정책 완화가 필요하면 그 차이만 보고한다. 독립적인 로컬 작업까지 멈추지는 않는다.

## 검사와 인계

- 버그 수정은 별도 입력이나 실패 주입으로 재현하고 원하는 동작을 회귀 테스트에 남긴다. 새 기능은 정상 사용자 진입→소비자→저장 결과를 확인한다. 테스트 double의 고정 답은 live 의미 품질 증거가 아니다.
- 현재 적용되는 검증 정책을 따르되, 개발 중에는 영향 범위의 검사부터 수행한다. 동일 source의 살아 있는 검증을 중복 실행하지 않는다. 검사 범위를 줄이려면 기존 정책을 조용히 무시하지 말고 변경안을 명시한다.
- 실패/중단/NOT_RUN을 정확히 보고한다. 관련 테스트 PASS와 전체 회귀 PASS, controlled D4와 live D5를 구분한다. 파일/RPC/테스트 개수를 완성률로 쓰지 않는다.
- step 완료는 key 존재가 아니라 exact input revision·실제 상태 전이·후속 소비로 증명한다. NOT_TRIGGERED/NO_CHANGE/NOT_RUN을 SUCCESS와 구분한다.
- 인계에는 해결한 기능, 실제 검증, 미완료/차단, 관련 source와 기록 위치를 남긴다. 제품 의미가 바뀌면 정본을 먼저, 해당 Wiki projection을 다음으로 갱신한다. 상태만 바뀌면 NOW/검증 기록만 갱신한다.
- 배포·외부 발송·공식 제출·새 credential/비용·물리 장비·commit/push는 사용자의 해당 실행 지시 범위를 따른다. 이 문서 자체는 그 권한을 부여하지 않는다.

## 상세 계약 길잡이

아래 경로는 저장소 루트 기준으로 읽는다. 모든 파일을 매 턴 전부 읽으라는 뜻이 아니다.

- 계층/owner/UoW: docs/architecture/backend-runtime-boundaries.md
- 제품 모델·도구·권한·Memory: docs/architecture/agent-execution-rules.md
- registry 계약: docs/architecture/extension-point-catalog.md
- 현재 검증 선택 정책: docs/architecture/verification-profiles.md
- 제품 기능의 고정 중심: research-briefs/CORE_PRODUCT_INVARIANTS.md
- 정상 진입/성숙도: PROJECT_WIKI/50_SEED_ROADMAP/behavioral-acceptance-contracts.md 및 implementation-maturity-matrix.md
- 이번 보완 범위: docs/plans/THOTH_POST_AUDIT_REPAIR_SCOPE_20260913.md
