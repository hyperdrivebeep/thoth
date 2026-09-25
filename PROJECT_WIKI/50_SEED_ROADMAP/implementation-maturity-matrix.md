---
id: IMPLEMENTATION-MATURITY-MATRIX
status: ACTIVE
page_role: implementation_truth_ledger
authority: ../../research-briefs/CANONICAL_RND_EVIDENCE_HARNESS_DESIGN.md
last_updated: 2026-09-20
---

# Implementation Maturity Matrix

이 문서는 설계 존재, RPC 등록, 실제 제품 동작을 혼동하지 않기 위한 구현 진실 원장이다. UI는 이번 판정 범위에서 제외한다.

## 성숙도 정의

2026-09-08 [A11 검사기 거래/복구 후속](../../docs/verification/a11-rule-transaction-final-20260908.md)은
개발 workflow 검증이다. 제품 A11 D4, owner debt 10 OPEN, N07/N08 및 field 상태를 승격하지 않는다.
최종 source/통과 여부는 해당 하위 계획의 완료 영수증으로 판정한다.

```text
D0 DESIGNED              canonical 설계에 요구사항이 존재
D1 SCHEMA_DEFINED        타입·상태·입출력 계약이 존재
D2 HANDLER_MATERIALIZED  handler가 객체·레코드를 생성하고 읽을 수 있음
D3 ISOLATED_TESTED       독립 단위·통합 테스트에서 해당 기능이 동작
D4 INTEGRATED_IN_LOOP    정상 upstream에서 자동 진입하고 downstream으로 자동 전달
D5 LIVE_E2E_VERIFIED     실제 모델·runtime·connector를 포함한 정상 사용자 시작점 E2E 통과
D6 FIELD_VALIDATED       외부 사용자·전문가·현장 결과로 유용성과 안전성 검증
```

`구현 완료`는 최소 D4를 뜻한다. D1~D3은 `계약`, `materialized`, `isolated pass`로만 표현한다. Adapter 자체의 D5와 그 adapter를 사용하는 제품 loop의 D5는 별도 행으로 기록한다.

## 현재 판정 — 2026-09-06 / 제품 baseline 1136e90

[구현 사실 교정](../30_ARCHITECTURE/current-truth-and-next-gap-20260906.md)이 과거 broad completion 문구를 대체한다.
아래 live D5는 해당 과거 source/fixture/route의 증거이며 이번 문서 교정이 live를 재실행한 것은 아니다.

| 기능 | 현재 수준 | 확인된 범위 | 부족한 연결·증거 |
|---|---:|---|---|
| Project 생성·격리·cutoff·SQLite persistence | D5 | CLI/HTTP와 canonical readback | multi-user actor authorization은 별도 항목 |
| Operator-grade human TUI | D0 | 2026-09-05 startup context·streaming progress·typed panels·palette/editing·drill-down·control 계약을 잠금 | 현재 `input()`+summary JSON shell은 engine 진입 증거일 뿐 operator UX가 아님; A01 RED부터 필요 |
| HWPX·PDF·DOCX·XLSX·구조화 텍스트 ingestion | D5 | artifact, source span, hash, warning 보존 | OCR·binary HWP는 명시적 경계 |
| Source Registry·lineage·authority·cutoff | D4 / 선택된 A02 live slice D5 | source/connect와 bounded Investigation의 source/span 등록 | S3/MCP general live, 공유 자료 ownership과 owner 전체 lifecycle은 미검증 |
| 자료 기준시점 판정 UX | D3 / runtime 연결 범위 D4 local | PDF/HTML 문서 날짜의 ELIGIBLE/AFTER_CUTOFF/UNKNOWN_TIME 서버 판정, artifact/span 일관성, 인용 시 UNKNOWN 확인 | 전체 parser 일반화·live 사용자 E2E·FULL은 미검증 |
| Evidence/Observation/Claim 후보 상태축 | D4 bounded | source-bound candidate와 Acquisition의 Observation/Lead/Claim UoW | 전체 public lifecycle와 공유 source ownership은 미완료 |
| 정보 충분성 reducer | D4 bounded | missing/conflict/counterevidence와 A02 자동 acquisition·재분석 연결 | general non-critical multi-wave와 arbitrary route 확장은 미검증 |
| Codex OAuth semantic adapter | D5 | 실제 OAuth schema canary와 synthetic main cycle | tool 사용은 의도적으로 금지; acquisition은 Coordinator 책임 |
| 질문 언어 사용자 prose 계약 | D3 local | Codex/OpenAI/xAI/Anthropic prompt wire와 prompt version/input digest 집중 검사; UI 한국어 진행 표시 Web 회귀 | 실제 한국어 질문→한국어 live 모델 응답 E2E는 NOT_RUN |
| 기존 근거 기반 경쟁가설 생성 | 기존 normal D5 / N02 양방향 통합 D4 | full Hypothesis/Portfolio owner와 candidate/read view 분리, normal/public 편집·재분석·history·restore/reopen·fault/CAS; receiptf34d65f2 | 새 live 증거 아님. Prediction/TestValidity 실연결은 N03 |
| 기존 근거 기반 다음 행동 비교 | 기존 선택된 normal D5 / N02 양방향 통합 D4 | full Action/Portfolio/Plan owner 공유, public draft 보존, 정상/R2/recovery 동일 identity와 보수적 권한; receiptf34d65f2 | 실제 시험 유효성·전체 owner closure는 N03/N07 |
| Investigation Contract | D4 | 정상 `thread/input`의 decision-critical gap에서 typed SearchIntent와 BOUNDED Investigation이 자동 생성되고 budget·stop·audit·reload readback 통과 | CRITICAL·SATURATION 실행은 A03/후속 범위 |
| Authorized Evidence Acquisition Loop | D5 local PostgreSQL / D4 general | actual Codex OAuth 자연어 TUI `thread/input` 1회에서 SearchIntent→A11-bound runtime-credentialed SELECT-only PostgreSQL approved view→Connector receipt→Observation/Lead/Claim candidate atomic gate→sufficiency·hypothesis/action 자동 재계산, terminal `SUFFICIENT`, manual semantic RPC 0, write/raw-table/raw-SQL denial과 cleanup 통과 | S3/MCP live product loop와 ULW general 다중 track·computed VOI·open-ended saturation은 미검증 |
| ULW식 다중 search track·expansion | D4 bounded CRITICAL profile | A03에서 복수 policy route, priority, independent/alternative/excursion track, content-digest dedupe, checkpoint/resume 자동 실행 | general non-critical A02 expansion과 arbitrary dynamic route는 미검증 |
| Lead Graph·VOI·saturation | D4 bounded CRITICAL profile | A03 wave별 typed Lead, computed VOI, confirmation/sufficiency·decision-rank 변화, duplicate exclusion과 deterministic convergence/general saturation | cross-project/global graph와 external live generalization은 미검증 |
| Counterevidence request | D4 | 정상 `thread/input`에서 high-risk/contested 우세 후보가 bounded multi-wave CRITICAL Investigation, 복수 A11-bound Connector route, deterministic independence/authority/temporal/context gate, 같은 Hypothesis revision diff와 non-truth receipt로 자동 연결 | policy-declared bounds만 검증; open-ended research 금지 |
| Local/Git/Postgres/S3/MCP Connector adapter | D5 adapter / D5 PostgreSQL A02 product slice / D4 other bounded slices | PostgreSQL은 actual OAuth 자연어 TUI의 A11-bound BOUNDED Investigation에서 실제 runtime credential·approved view로 자동 호출; 개별 adapter live·contract와 수동 connect도 유지 | S3/MCP autonomous live E2E와 general multi-wave는 미검증 |
| 프로젝트 공개 웹 동의·허용 host 실행 경계 | D3 local | workspace grant→project policy 결속, stale/revoked grant I/O 전 차단, managed allowlist/egress, 등록된 arXiv 진입점·same-host 후보와 자유형 query 거부를 단위/통합 검사 | 실제 공개 인터넷 취득·live 연구 loop·FULL·commit/push는 NOT_RUN |
| Project/environment connector·sandbox policy integrity | D4 | 정상 `project/source/connect`·`execution/start`에서 authoritative ProjectPolicy revision/digest, empty allowlist, security ceiling, egress를 I/O 전 검증하고 typed denial·무변경 readback·atomic Acquisition UoW 통과 | deployment/field NOT_RUN; unified external-runtime receipt DAG는 A10 |
| Action/Execution 상태기계 | D4 R2 slice | 정상 `thread/input`의 safe-frontier R2 ActionCandidate가 typed SandboxRunSpec·policy/head/input preflight·sandbox·Observation·Outcome/Hypothesis/next-action revision으로 자동 연결 | A05 bounded recovery와 R3 authorization은 별도 |
| Docker·gVisor·Firecracker·E2B sandbox adapter | D5 adapter / E2B 선택된 A04 D5 | 개별 live run과 N02 6G normal Thread→E2B→Outcome 증거 | Docker/gVisor/Firecracker normal-loop D5는 별도 미검증; adapter D5를 전이하지 않음 |
| R2 closed loop | D4 | Planner candidate→policy template compiler→current Head/input digest preflight→sandbox→non-truth Observation→Outcome validity→neutral Hypothesis appraisal→next-action order·3-revision atomic commit | A05 retry/reconciliation과 live adapter product E2E는 별도 |
| Outcome과 same-Thread 재분석 | D4 | 새 evidence 재분석과 A04 execution-linked neutral appraisal/next-action revision | comparator·criterion·causal truth는 의도적으로 NOT_ASSESSED |
| N03 봉인 예측·범위 내 시험 평가 | D4 로컬 정상 연결 완료 | thread/input→source-bound Prediction→실제 Execution/attempt→원자료 평균 계산→같은 가설 appraisal→다음 행동; invalid/fault/CAS/reopen/restore, full765/Web3/doctor 두 차례 exit0와 receiptca0b850a | TEST_ONLY 프로토콜 증거, RANGE/ARITHMETIC_MEAN 한정; 과거 cutoff 적격성 미상은 HOLD. 새 live D5·공식 Criterion·인과 진리·owner 전체 원자성 주장이 아님 |
| Revision·diff·restore | D5 | live CLI/HTTP cycle과 dependent invalidation/recalc | 없음; merge는 별도 항목 |
| Semantic branch | D4 | expected-head 충돌과 counterevidence branch | 일반 동시작업·멀티 actor 실시간성은 미검증 |
| Semantic three-way merge | D4 | public ChangeSet sibling branch 보존, canonical common ancestor 기반 독립 field-path auto merge, protected/schema/domain overlap OPEN_CONFLICT, typed non-truth receipt | authenticated real multi-user concurrency는 A13; domain-specific validator profile은 A09/extension 범위 |
| Multi-Baseline normal loop | D4 | normal Thread ProjectHeadSet/scoped candidate, protected BaselineSet decision, Outcome/Improvement/Closure/Export comparator, routine change/restore invalidation | official milestone authority는 protected; external milestone use NOT_RUN |
| Criterion Contract compiler | D4 bounded | source candidate·산식·단위·조건·authority와 등록 profile | 모든 domain 일반화 아님; 조건부 reference range/interview의 정상 실행 증거 없음 |
| Dynamic Criterion/Profile router | D4 | six versioned YAML packs가 same normal Thread core를 사용하고 source applicability evidence, latest-version selection, non-system GENERAL_RND continuation, specialized/ambiguous decision-dimension 차이 PROFILE_DECISION_REQUIRED/HOLD를 통과 | 외부 domain authority activation과 field vocabulary는 registry extension/owner decision |
| Basic project-scoped memory | D4 | project isolation, append-only history, basic recall | full lifecycle와 같은 normal Thread 경로에서 회귀 검증 |
| Facts·Reflection·Dream·Team memory lifecycle | D4 | 정상 Thread 결과의 typed 4-role review→COMMIT/REVISE/HOLD/QUARANTINE, 다음 same-project Thread 자동 relevance recall, authority/cutoff/support/action gate, poisoning/conflict exclusion, atomic rollback과 reopen readback | 외부 live multi-user memory는 NOT_RUN; vector/summary는 rebuildable non-canonical projection |
| Improvement 관리·저장 loop | D4 bounded management | repeated failure→candidate/baseline/typed registry→current-basis finalize→DB active pointer/rollback; 기본 평가기 미설정은 HOLD | 고정 점수는 명시적 TEST_ONLY 관리 회귀 전용. 실제 비교/정책 적용은 아래 N05/N06의 별도 증거 |
| 실제 baseline/candidate 비교 실행 | N05 bounded D4 local | 정상 Thread의 명시적 runnable plan→실제 분리 subprocess pair→독립 scorer→공개 평가, 현재 권한·예산·재시작; backend837/Web3와 receipt289b096d | PURE_TRANSFORM_V1의 명시적 계획 비교 범위. 자동 실행 후보 생성과 실제 적용은 N06, live D5 미실행 |
| 실제 behavior activation·shadow/canary | N06 bounded D4 local | prompt·검색·의미보완 실제 소비, 공통 정책 subprocess 비교, 자동 제한 후보, scoped exposure·별도 승인·관측·budget/CAS/rollback/reopen; final backend860/Web3/full gate, receipt4a5fea12 및 봉인 source 일치 | 정책 shadow만 관측; 실모델 품질 NOT_ASSESSED/live D5 미실행; weights/R4 제외. Owner10 전체 closure는 N07 |
| 조건부 reference-range 후보·인터뷰 | D0 설계 | canonical 3-lane와 applicability/uncertainty 계약 | source packet·결정론적 비교/계산·질문/응답 revision·정상 사용자 loop 미구현 |
| Canonical receipt·revision integrity | D5 | digest verification, revision/closure/export receipt | external signature/TSA는 deferred |
| Connector/Sandbox unified receipt DAG | D4 foundation | typed runtime/cycle nodes, canonical DAG/manifest/bundle/verification, separated trust axes, corruption/missing-parent detection과 closure/export local boundary | external signature/TSA와 실제 R3 release는 NOT_RUN |
| Closure·LOCAL_SEALED export | D5 | live local lifecycle과 manifest/receipt | external release는 R3 경계 |
| Universal multiplayer semantics | D4 local | credential-verified session→ACTIVE role/capability/scope request-time revalidation, project-list isolation, protected authority/actor/session/receipt attribution, stored Thread pre-claim scope, authenticated concurrent branch/merge; N04 D02/B Source·derived·memory·query·receipt·operation·export 공유/회수 local D4, backend809/Web3 및 receipt0c94759b | N04 영수증은 해당 봉인 source 범위다. 새 N05 변경의 전체 증거로 승계하지 않음; external institutional SSO와 deployed multi-user concurrency는 NOT_RUN |
| Field measurement tooling / usefulness·time saving·WTP | D5 local replay tooling / D1 field | six-case sealed baseline import, 6×6 counterbalance, pseudonymous session/event/score, normal RPC instrumentation, timer/tool/manual burden replay, blind adjudication and privacy-safe export with forced NOT_RUN | 실제 participant consent/session, independent expert score, measured time saving·WTP는 NOT_RUN; D6 아님 |

## 현재 전체 판정

```text
CONTRACT SURFACE: BROAD
LOCAL CORE: PARTIAL WORKING
INTEGRATED AUTONOMY: PARTIAL
LIVE ADAPTERS: VERIFIED SEPARATELY
FIELD VALIDATION: NOT_RUN
PRODUCTION: NO_DEPLOY
```

```text
ATOMICITY DEBT: 10 OPEN
bounded slice D4/D5 does not certify full namespace atomicity
```

Manifest의 9 `PARTIAL` owner와 1 `MATERIALIZED_ONLY` owner는 각자 debt ID, owning Acceptance와
closure fault-injection evidence를 가진다. 이 표시는 예외 승인이나 구현 완료가 아니며, 실제 atomic
UoW receipt 없이는 `ATOMIC`으로 승격하지 않는다.

## Post-D4 phase ledger — 역사적 실행 증거

이하 기록은 당시 source의 좁은 성공 범위를 보존한다. A08/P6의 stage 이름·registry 변경을
실제 평가/적용 완료로 해석했던 부분은 위 현재 판정과 TRUTH-G02가 대체한다.

G01/G03 Hook revision은 interpreter mutation 분류, canonical preflight 검증, create-only receipt archive, verified scope와 wiki-sync 재검증을 focused `30 passed` 및 backend `363 passed` 두 차례로 검증했다. Fresh project task에서 SessionStart·allow·preflight-less deny·probe absent·receipt binding과 non-blocking completion을 관측했다. Hook은 D-level 승격 증거가 아니며 동일 OS 사용자 악성 프로세스를 막는 보안 경계도 아니다.

| Phase | 상태 | 근거 | 성숙도 영향 |
|---|---|---|---|
| P0 POST_D4_BASELINE | PASS | baseline `bbb597f4d1e9ace15ab8a1b61249b1a13d4a44ad4affb9de37d1c3d7c06aa5a6`, receipt `ff92cfaec7c2a79195b3074383341181498d1671ff5a79983cc1f2973068435e` | 기존 A01-A13 level 재정의 없음 |
| P1 RPC/wiki truth | PASS | 303 canonical + 6 aliases = 309 runtime, 219 notifications; receipt `21bc50b442f5081f41afebbe45e606db6992ea3571bf7772ee0844a2d950e139` | catalog materialization이며 behavioral D-level 승격 아님 |
| P2 Multi-Baseline | D4 | normal-loop receipt `670d75a0d1b6e4716d3fc6737b88cf1b4d2c534facaeb317cd7f3e3fab1532b3` | milestone decision protected |
| P3/C08 real Connector lanes | D5 local PostgreSQL / D4 other lanes | normal A02 network-share receipt `b26ae98ddc438c779aaa9697a347a99d4512b1ea0124bd916196aec7d164df2a`; C08 actual OAuth PostgreSQL trace `90536eadbc404e8b44be96d35c90c38bf180b2dfbe0c4640a6f85b545eaa280e` | credentialed S3/MCP live product loop NOT_RUN |
| P4 Sandbox router | D4 scripted/router | exact-profile receipt `53f079bb24d11bdfde878ace8952ea335783e1fbeeb31f25897613d68d31140b` | Docker/gVisor/Firecracker/E2B live D5 NOT_RUN |
| P5 semantic Memory | D4 local adapters | receipt `b335a26a64c25d10de3d9da113bbd0ba6638435b13929efc395e87b6f1c59fe1` | real-model reviewer D5 NOT_RUN |
| P6 Improvement 관리·artifact registry | D4 관리 범위, 실제 적용 미완료 | receipt `3c7b4172d2f554fc6e49d497bab8805da2ad4277a459c262bd833d88dc05668a` | external evaluator/production traffic and weight changes NOT_RUN |
| P7 six Criterion Profiles | D4 same core | receipt `0af3312ff1b776ae52399668947bc1b62164253595abcf44788b3d2f63a234b5` | external profile authority decisions/field use NOT_RUN |
| P8 natural-language TUI | D5 local on 6G OAuth / D4 fixture generalization | P8 receipt `220868cae204f1c96ace5e34a735dde31223c007835fde90fa6724f1ef5c972b`; P10 OAuth receipt `b24a8f30c99d9ad614c8705edf2ab4f931c51c00fcf6d9025c0a54be282b8410` | public OAuth was P10 LIVE_HOLD and later closed by N01B; full 16-screen UI out of scope |
| P10 current Hero/secondary/holdout | 6G D5 local; public/SUNRISE D4 sealed; holdout evaluator PASS at P10 | 6G real OAuth manifest, same-trace sealed manifests, independent OpenDreamKit oracle evaluation; receipt `b24a8f30c99d9ad614c8705edf2ab4f931c51c00fcf6d9025c0a54be282b8410` | public synthetic OAuth was LIVE_HOLD at this historical gate; N01B later supplied D5 local evidence |
| P9 A12 field execution tooling | D5 local replay tooling / D1 field | sealed baseline `e628bd1216b6e2e51beaa4220b76f89df1559c48702c3b32c41fb9ce06bb1f29`; receipt `5665b86f9700928d596423336f8fe4d2fbf7903888145b1b937f4a357255ea31` | participant/expert sessions, measured time saving and WTP remain D6 NOT_RUN |
| N01A public OAuth diagnostics | D4 diagnostic integration; A01 level unchanged | generic public error, privacy-safe diagnostic ledger, fresh OAuth fault fingerprint; receipt `73b8b0fa944de74d74b7c68a0490d2392536db454a9287684823b2a445a151f8` | concrete `_content_excerpt` TypeError found; public OAuth remains LIVE_HOLD until N01B |
| N01B public OAuth exact fix | D5 local A01 public synthetic path | actual OAuth natural TUI→ChangeSet→Memory→Receipt manifest `146ed34fb7aa20077207cce556b719cd712aae6f6d755f165768a32137464471`; receipt `819c2ccd6ee8b8129a1949f2872d56fa00e2d00248106c62af2571b286352381` | deployment, institutional identity and field evidence NOT_RUN |
| N02 managed E2B normal Thread | D5 local A04 on 6G | actual OAuth natural TUI→normal `thread/input`→actual E2B MANAGED/DENY_ALL→Observation/Outcome/Hypothesis/Action revision; cleanup DESTROYED, active 0; manifest `3cd2ec366b82f9955ed24770f2dbca5fbcf9093beede5c1547e1c0786a1c5df0`; receipt `f3f4b3e0fd60148a64b490377e87df87aa91098e900e6a7ba365aaa1348542b6` | public-membrane fresh attempt held before sandbox on semantic alternative contract; deployment/field NOT_RUN |
| N03/C08 credentialed Connector | D5 local PostgreSQL A02 / S3·MCP NOT_RUN | runtime-generated credential, SELECT-only role, approved view, actual OAuth natural TUI→SearchIntent→A11→PostgreSQL→Evidence UoW→reanalysis; cleanup and secret scan PASS | S3/MCP live product loop, deployment and institution credentials NOT_RUN |
| N04 real-model Memory reviewer | D5 local A06 on 6G | actual Codex OAuth Facts/Reflection/Dream/Team, deterministic override/reducer, model/schema/input/output/basis digests, same-project recall 1/cross-project 0; manifest `0c2a0ff3e03296a967672eba72e6d7eee5ac9b72983542222b437996f3970154`; receipt `a31297dbf741242ec464f61b91191cddd46ace7c0bcc3172ed94188b28e09c9c` | same-GPT roles are not institutionally independent; deployment/field NOT_RUN |
| N05/C07 evaluator extension | D4 local registry / external process DEFERRED | typed capability registry가 normal A08 loop에서 budget·timeout·schema/digest를 검증하고 candidate-only 결과를 반환 | opaque holdout transfer, subprocess/Sandbox external evaluator와 production traffic unopened |
| N06 audit checkpoint | AUDIT complete / checkpoint NOT READY | architecture/full verify/doctor/field/diff/dependency/security scan; security inventory 228/228 | 25 open findings, 4 deferred external-control gaps; no commit/release |
| N07 external field | D1 field / D6 NOT_RUN | sealed local field tooling only | consented humans, 36 sessions, blind scores, time, demand and WTP absent |
| G01 Hook/gate hardening | code/contract VERIFIED; new runtime trust NOT_RUN | A11 preflight `45d8f20bd66dc1f23a969cdbc75e26a620e5ab8fd569ef05771b48dae09edcc1`; completion `7bdd536b00c81cbeac34011ffbb3fb88e38f94827b57efafe8987e6cfa573bed`; backend 361 twice | fresh Codex session에서 changed hooks.json 재신뢰와 live allow/deny/Stop 관측 필요; product A11 D4 unchanged |
| G02 authenticated Receipt hardening | A10/A13 D-level unchanged; regression FIXED_LOCAL | authenticated actor/session/role canonical digest binding, pre-insert spoof denial, local PARTIAL attribution, unknown bundle selector fail closed; receipt `bbc716c1960fa4e3e86b15929b3742cfd65ff32565bff186a28e1204f16900a1`; backend 361 twice | unauthenticated loopback has no verified identity; institutional auth/signature/TSA NOT_RUN |
| G03 wiki-sync Hook/runtime | workflow guard runtime PASS; product D-level unchanged | wiki-sync `ec027c4dd512197b5b455605104a616cf67b5d5888ab04edaffaede02d9f71d8`; completion `9db919d1203c12bc415953016477ac3952471cf75b84d7ee6c81409dbc2e8f5c`; backend 363 twice; fresh task allow/deny/probe/receipt/completed PASS | semantic correctness still requires drift audit; same-OS malicious process outside boundary |
| A11 architecture truth enforcement | PASS; product D-level unchanged | registry/factory/core-closed/doc-drift guards, actual extension targets, atomicity debt 10/fully_atomic false, backend 428 twice; receipt `17a60b677719f18b7e3cafaa87908bceac4e99297abb1aa23ef455c06b10757f` | does not implement or close the ten UoW debts |

## 기능군별 인수계약 매핑

| 기능군 | 인수계약 |
|---|---|
| Project·parser·Source Registry·Evidence·OAuth·sufficiency·hypothesis·action proposal | A01 |
| Investigation·Authorized Acquisition·ULW track·Lead·VOI·Connector product loop | A02 |
| Counterevidence·Critical review | A03 |
| Action/Execution·Sandbox·execution-linked Outcome | A04 |
| 실행 오류 taxonomy·retry·candidate revise | A05 D4 — transient-only bounded retry, semantic/code candidate branch, ambiguous reconciliation, budget exhaustion baseline preservation |
| Basic/Full Project Memory | A06 |
| Revision·branch·merge·restore | A07 |
| Recursive Improvement | A08 |
| Criterion Contract·dynamic profile | A09 |
| Receipt·Closure·Export | A10 |
| Connector/Sandbox policy integrity | A11 |
| Field usefulness·time saving·WTP | A12 |
| Universal multiplayer actor binding | A13 |

인수계약 원문은 [Behavioral Acceptance Contracts](behavioral-acceptance-contracts.md)에서 관리한다.

`303 canonical callable + 6 compatibility aliases = 309 runtime methods / 219 notifications`는 D1~D2 surface materialization 증거다. 개별 테스트가 있으면 D3까지 올릴 수 있지만, 그 숫자만으로 D4 이상의 제품-loop 완성을 주장하지 않는다.

## 갱신 규칙

1. 각 행은 대응하는 행동 기반 acceptance contract를 가져야 한다.
2. D4 승격에는 정상 upstream 자동 진입과 downstream 자동 전달 증거가 모두 필요하다.
3. D5 승격에는 수동 내부 객체 조립이 아닌 정상 사용자 시작점 E2E가 필요하다.
4. Adapter의 live pass를 제품 loop의 live pass로 전이하지 않는다.
5. 실패·HOLD·ABSTAIN 경로도 동일한 성숙도 증거에 포함한다.
6. 상태 변경 시 명령, fixture, digest, 결과와 잔여 미검증 범위를 함께 기록한다.
