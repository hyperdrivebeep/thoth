---
id: BEHAVIORAL-ACCEPTANCE-CONTRACTS
status: ACTIVE
page_role: preimplementation_acceptance_contract
authority: ../../research-briefs/CANONICAL_RND_EVIDENCE_HARNESS_DESIGN.md
last_updated: 2026-09-07
---

# Behavioral Acceptance Contracts

이 문서는 제품 코드를 쓰기 전에 정상 사용자 시작점, 자동 전이, terminal evidence를 고정한다. 현재는 명세이며 실행 테스트 코드가 아니다. UI는 범위에서 제외한다.

## 공통 완료 규칙

각 scenario는 다음을 모두 증명해야 D4 이상이다.

```text
normal entry
→ canonical input readback
→ automatic upstream/downstream transition
→ policy/schema/authority gate
→ success 또는 typed HOLD/ABSTAIN/FAIL
→ immutable revision/event/receipt
→ reload 후 동일 상태
```

내부 RPC를 사람이 순서대로 직접 호출하거나 테스트가 최종 객체를 미리 조립하면 D3 이상 증거로 사용하지 않는다.

## A01 — Existing-evidence reasoning loop

**Given** 사용자가 project를 만들고 권한 있는 계획서·결과 문서를 연결한다.  
**When** 같은 Thread에서 막힌 문제를 한 번 입력한다.  
**Then** THOTH가 source span, sufficiency, competing hypotheses, counter-search obligations, action alternatives와 Working Revision을 자동 생성한다.  
**Terminal evidence** project/thread/object/hypothesis/action/revision/receipt readback과 동일 OAuth model-call evidence.  
**현재 판정** D5.

### A01-TUI — Operator-grade human surface extension

**Given** workspace에 Project가 하나 이상 존재하고 사용자가 별도 QA scenario가 아니라 Windows Terminal/PowerShell에서 `thoth`를 직접 실행한다.  
**When** 사용자가 bare prompt에 막힌 문제를 자연어로 입력한다.  
**Then** 시작 화면은 active workspace/project/thread, model route, connector/sandbox capability와 policy/authority 경계를 secret 없이 표시한다.  
**And** 긴 실행은 context restore→thread/input→model→acquisition/counter-search→sandbox→revision/memory/receipt의 단계·상태·경과시간을 기존 operation/event에서 projection한다.  
**And** terminal 결과는 evidence/source span, competing hypothesis, next action, HOLD/ABSTAIN, revision/memory와 receipt/trace를 구분해 표시하고 drill-down을 제공한다.  
**And** command palette, history, multiline editing, pause/resume/cancel이 현재 operation state에 맞게 동작한다.  
**금지** raw prompt 저장, private chain-of-thought 노출, 별도 CLI truth/state machine, R3/R4 자동 실행, progress를 receipt truth로 오인, QA manifest를 사용자 TUI로 대체.  
**Terminal evidence** 실제 bare `thoth` PTY transcript, ordered progress projection, typed final panels와 drill-down, operation/receipt readback, redaction scan, interrupt/resume/cancel negative paths.  
**현재 판정** D0 DESIGNED. 기존 A01 engine D5와 별도이며 소급 하향하지 않는다.

## A02 — Missing evidence to autonomous acquisition

**Given** A01 결과에 decision ranking을 바꾸는 `dataset_version` 또는 동급 필수정보가 빠져 있고, Project Policy가 read-only connector를 허용한다.  
**When** sufficiency reducer가 해당 gap을 확정한다.  
**Then** 별도 사용자 명령 없이 typed SearchIntent, BOUNDED Investigation, query families와 stop contract가 생성되고 Coordinator가 허용 Connector를 실행한다.  
**And** 결과가 Observation→Lead/Claim candidate gate→Evidence Graph로 들어가며 sufficiency와 hypothesis/action ranking이 다시 계산된다.  
**Else** connector 소진·budget·authority gap이면 최소 질문 하나 또는 typed HOLD/ABSTAIN으로 끝난다.  
**금지** GPT의 임의 filesystem/network tool access, 권한 밖 route, 발견 즉시 공식 Claim 승격.  
**현재 판정** D5 local PostgreSQL product slice / D4 general. Fresh local workspace에서 actual Codex OAuth 자연어 TUI `thread/input` 1회가 project-policy-defined `dataset_version` gap을 typed SearchIntent·BOUNDED Investigation으로 만들고, A11-bound runtime-credentialed SELECT-only PostgreSQL approved view→Connector receipt→Observation/Lead/Claim candidate Evidence UoW→같은 Thread sufficiency·hypothesis/action 재계산까지 자동 연결했다. Terminal `SUFFICIENT`, manual semantic RPC 0, DB write/raw-table/raw-SQL denial, credential 비기록과 container cleanup을 확인했다. Manifest trace digest: `90536eadbc404e8b44be96d35c90c38bf180b2dfbe0c4640a6f85b545eaa280e`. 기존 policy HOLD, Evidence UoW fault rollback과 runtime reopen readback 증거도 유지한다. S3/MCP live product loop, ULW general multi-track·computed VOI·open-ended saturation은 후속 범위다.

## A03 — Critical counter-search

**Given** 현재 우세 가설이 high-risk 또는 contested로 분류된다.  
**When** counterevidence obligation이 열린다.  
**Then** CRITICAL Investigation이 독립 source territory와 alternative explanation track을 실행하고, source independence·authority·temporal validity를 검증한다.  
**And** support/counterevidence diff가 같은 Hypothesis revision에 반영된다.  
**Terminal** supported/refuted-within-scope/unresolved 중 하나와 non-truth receipt.  
**현재 판정** D4. 정상 `thread/input`에서 deterministic high-risk/contested leading candidate가 CRITICAL Investigation을 열고, typed Challenger와 Independent Gate Reviewer가 project scope·source independence·authority·cutoff·forbidden-context·명시적 match contract를 검증한다. Bounded multi-wave 확장은 복수 policy route, independent/alternative/excursion tracks, priority·content dedupe·computed VOI·checkpoint/resume·wave별 sufficiency/decision-rank 변화와 hard budget을 포함한다. 같은 Hypothesis/portfolio ID의 새 revision과 `semantic_truth_certified=false` receipt를 유지하며 기존 9 terminal 경로와 `SEARCH_SATURATED`·`BUDGET_EXHAUSTED`·`POLICY_BLOCKED`·`AUTHORITY_REQUIRED`·`ABSTAINED` 확장 경로를 통과했다. Base receipt `38712d204c78e8b2713b59939c05bacb50e779ffde6f5404ede2c75d26207e3a`; multi-wave extension receipt `542afd8b4f81226bda980f5341cb89c322f35bae46949fe34de317d8d698f54d`.

The A03 bounded storage follow-up groups public Hypothesis/Evidence writes and binds current
revisions, projections and audit digests. Combined60 and full backend640/Web/doctor passed;
immutable completion passed with receipt
`6f5e8ffac15e5df0a56786fa5a261028e7d0c0c41b2c71c591dd20895d053f52`. See the
[A03 storage record](../../docs/verification/a03-storage-atomicity-20260905.md).

THOTH-NEXT N02는 사용자 승인 D01의 full Hypothesis/Portfolio·Action/Portfolio/Plan owner를 정상
thread/input과 public read/edit에 연결했다. 후보·sealed record 구분, immutable legacy snapshot,
current head/history/restore/reopen, scope·CAS·중간 fault와 전체740/Web3/doctor가 통과했다.
[N02 검증](../../docs/verification/next-n02-identity-20260906.md)의 완료 gate도 full740/Web3/doctor와
receipt `f34d65f2d535352405a02d2b36444d95f2bf884117744e7afc5a1f67f1fd6e05`로 완료됐다. 양방향 로컬 통합 D4다.
새 live D5, 실제 시험 validity/appraisal 연결, owner 전체 원자성 완료로 해석하지 않는다.
Whole-owner lifecycle remains unresolved; the N02 receipt closes the bounded normal/public identity gap.

착수 시 A03 연결 간극: normal portfolio와 public HypothesisRecord가 같은 HYPOTHESIS head 공간에서
다른 schema를 사용해 public record를 normal reader가 읽을 때 ValidationError가 재현됐다.
이 identity 문제는 N02 증거로 해소했다. N03은 관측 전 sealed Prediction, 실제 Execution/attempt,
독립 계산한 TestValidity/fit, 같은 가설 appraisal과 다음 자연어 행동을 연결한다. 무효·POST_HOC·
미검증 가정은 지지로 승격하지 않으며 stale/duplicate/fault/restore/reopen을 검사한다.
2026-09-07 묶음66개와 cutoff 보완 집중8개, full765/Web3/lint/type/build/doctor가 exit0으로 통과했다.
완료 gate도 full765/Web3/doctor exit0, receiptca0b850a와 봉인 source 일치로 확인했다.
정상 로컬 D4 프로토콜 연결 증거다. 더 이른 cutoff는 시간 적격성을
추정하지 않고 명시적 HOLD하며, 현재 Project cutoff의 봉인된 예측을 지원한다.

## A04 — R2 action closed loop

**Given** Action Planner가 policy-allowed R2 후보를 safe frontier로 선택한다.  
**When** Coordinator가 Action Candidate를 SandboxRunSpec으로 컴파일한다.  
**Then** authoritative project-policy digest, current HeadSet, input artifact digest와 runtime capability를 검증한 뒤 sandbox를 자동 실행한다.  
**And** result를 Observation으로 봉인하고 Outcome validity, hypothesis appraisal, next-action ranking과 Working/Candidate Revision을 자동 갱신한다.  
**금지** R3/R4 자동 실행, sandbox의 canonical DB 직접쓰기, 같은 semantic revision 무한 재실행.  
**현재 판정** D4. 정상 `thread/input`의 safe-frontier R2 ActionCandidate가 policy-owned SandboxRunSpec compiler, authoritative policy/current HeadSet/exact input digest preflight, automatic sandbox, non-truth Observation, typed Outcome validity, neutral Hypothesis appraisal와 next-action ActionPlan revision으로 자동 연결된다. Outcome·Hypothesis·Action revision은 한 changeset/receipt로 커밋되며 process success는 scientific truth·criterion attainment와 분리된다. Policy HOLD와 Action semantic UoW rollback 통과. Verification receipt: `115e4fd3c694c28d852c8d27e716f456f165585db4cb8e4f7a7060084741bb5b`.

**N02 추가 판정** 6G local product path D5. Natural TUI→normal `thread/input`→actual E2B
MANAGED/DENY_ALL→Observation/Outcome/Hypothesis/Action revision을 통과했고 cleanup DESTROYED,
active sandbox 0을 확인했다. Manifest `3cd2ec366b82f9955ed24770f2dbca5fbcf9093beede5c1547e1c0786a1c5df0`;
completion receipt `f3f4b3e0fd60148a64b490377e87df87aa91098e900e6a7ba365aaa1348542b6`.

## A05 — Failed execution and bounded recovery

**Given** A04 실행이 semantic/code/infra 오류 중 하나로 실패한다.  
**When** failure taxonomy가 확정된다.  
**Then** transient infra만 제한 retry하고, semantic/code failure는 새 candidate revision과 수정 근거를 만든다.  
**And** 개선되지 않으면 원 baseline을 유지하고 typed failure receipt로 끝난다.  
**현재 판정** D4. 정상 `thread/input`에서 transient infra만 bounded retry하고 semantic/code failure는 repair-basis candidate branch revision, ambiguous external result는 no-auto-retry reconciliation, retry exhaustion은 baseline Head 보존과 typed non-truth failure receipt로 종료한다. Verification receipt: `6027188a22887b88039a93284e39b0c9b71df7b10e54971ca1a0022e3aaebf12`.

## A06 — Project Memory promotion and recall

**Given** Outcome과 revision에서 재사용 가능한 사실·교훈·가설 후보가 생성된다.  
**When** Memory policy가 payload mode, recall class, risk와 transition을 평가한다.  
**Then** 필요한 Facts/Reflection/Dream/Team 역할이 실제 독립 검토를 수행하고 reducer가 commit/revise/hold/quarantine 중 하나를 선택한다.  
**And** 다음 같은-project Thread의 query에 relevant·cutoff-valid·authority-valid memory만 주입된다.  
**금지** cross-project 자동 recall, ambiguous/conflicting memory의 Action Context 주입.  
**현재 판정** D4. 정상 `thread/input`이 reusable Hypothesis/Action/Outcome revision을 typed Facts·Reflection·Dream·Team 독립 검토로 보내고 reducer가 COMMIT/REVISE/HOLD/QUARANTINE을 결정한다. 다음 same-project Thread는 query relevance·scope·current owner revision·authority·cutoff·support·별도 recall/action eligibility gate를 통과한 memory만 자동 주입한다. Poisoning/secret-shaped content redaction, conflict/ambiguous HOLD, cross-project exclusion, cycle head+memory atomic rollback, runtime reopen readback과 keyword/vector/graph/summary non-canonical rebuild를 통과했다. Verification receipt: `d6c3818c673cf78324b7b90a981e58fafeb0f28b7d27db80adf6eb3915064297`.

**N04 추가 판정** 6G local product path D5. Actual Codex OAuth role reviewer가 model/prompt/
input/output/schema/basis digest를 남기고 deterministic override/reducer만 transition을 결정한다.
Required-role failure는 HOLD이며 동일 GPT 역할은 institutional independence를 주장하지 않는다.
Same-project recall 1, cross-project 0 manifest
`0c2a0ff3e03296a967672eba72e6d7eee5ac9b72983542222b437996f3970154`;
completion receipt `a31297dbf741242ec464f61b91191cddd46ace7c0bcc3172ed94188b28e09c9c`.

The local A06 storage follow-up separates typed preparation, review/projection I/O and one short
current-basis final commit. Initial and reanalysis head conflicts preserve the semantic branch and
stop downstream use; concurrent successful memory, authority/session drift and fault/reopen controls
are covered. Backend548/Web/doctor and completion passed with receipt
`142c224fc2352ff64378d4cef3fd71a44efe6bc571c2229d73cc6cace28f8704`. See
[A06 storage verification](../../docs/verification/a06-memory-storage-boundary-20260905.md).
This preserves the bounded maturity level and does not rerun or extend N04 live evidence.

## A07 — Branch, semantic merge and restore

**Given** 같은 parent에서 서로 다른 actor/agent 변경이 발생한다.  
**When** 두 ChangeSet이 Working Head를 갱신하려 한다.  
**Then** 둘 다 branch로 보존되고 common ancestor 기준으로 schema·authority·cutoff·dependency·domain policy를 통과한 독립 필드만 자동 merge한다.  
**And** 충돌은 OPEN_CONFLICT로 남으며 restore는 새 revision으로 생성되고 dependents가 invalidate/recalculate된다.  
**현재 판정** D4. Public revision proposal/change-set 경로가 같은 parent의 stale candidate를 immutable `BRANCHED` revision으로 보존하고, canonical DAG common ancestor 대비 한쪽만 변경한 field path만 자동 merge한다. Overlap과 authority/cutoff/policy/security/dependency/criterion/disposition, schema mismatch, domain type mismatch, stale merge head는 typed `OPEN_CONFLICT`와 non-truth receipt로 종료하며 head를 바꾸지 않는다. Restore는 과거 snapshot을 새 child revision으로 만들고 dependent를 같은 transaction에서 `RECALCULATION_REQUIRED`로 갱신하며 stale expected head를 pre-write 거부한다. Verification receipt: `a7d82bf88bb6e571a0697a3b54fba4fa14a278edf0312662d4f2205a5e8ac152`.

## A08 — Recursive improvement canary and rollback

**Given** 동일 failure pattern이 policy threshold 이상 반복된다.  
**When** prompt/retrieval/workflow/evaluator 후보가 생성된다.  
**Then** immutable baseline과 candidate를 같은 frozen fixture·external evaluator·hidden holdout에서 실행하고 품질·안전·비용을 비교한다.  
**And** exact-digest canary에서 guardrail을 통과할 때만 promotion 후보가 되며 no-improvement·critical regression·leak·timeout이면 자동 rollback한다.  
**금지** 공식 KPI·안전 임계값·waiver·model weight 자동 변경.  
**현재 판정(2026-09-06 교정)** 관리·저장 loop만 bounded D4. 반복 실패에서 candidate/baseline, registry 호출, 현재 권한·시간 검사, active digest와 rollback 기록은 연결됐다. 기본 evaluator는 실제 paired execution 없이 품질5000→7500 등 고정값을 반환하는 test double이다. OFFLINE/SANDBOX/SHADOW/CANARY label 생성은 각 stage 실행 증거가 아니며, 활성 artifact 내용을 실제 model/retrieval/workflow가 소비하는 통합도 미완료다. 위 Given/When/Then의 전체 인수는 아직 충족하지 못했다. 과거 receipt `bab78e78104d3a1e7a765e754ffcd996681221e929aa2fb184aa9ee0b664f89c`는 좁은 관리·저장 검증으로 보존한다. 자세한 현재 근거는 [TRUTH-G02](../../research-briefs/THOTH_IMPLEMENTATION_TRUTH_20260906.md)를 따른다. 실제 canary/최종 baseline의 승인 경계는 canonical §14.2를 따르며 routine R0/R1/R2 전체를 결재 대상으로 바꾸지 않는다.

The A08 storage-boundary follow-up is specified in
[`THOTH-STORAGE-20260905-01`](../../research-briefs/THOTH_STORAGE_PHASE_BOUNDARIES_20260905.md).
Its phase-aware failure/request persistence supersedes the old evaluator-fault observation-deletion
test expectation. The historical A08 receipt does not certify this follow-up. The new normal-entry
session expiry, real SQLite lock deadline, active-baseline preservation, public evidence binding
and fault/reopen regressions passed, followed by backend 525/Web/doctor. See the
[A08 verification record](../../docs/verification/a08-storage-authority-20260905.md); its immutable
completion rerun exposed a shared Operation claim race, corrected under a replacement preflight
with normal idempotency/CAS and unchanged R3 regression checks. Replacement backend 528/Web/doctor
passed, then completion sealed the source with receipt
`38280c8553661116b8794daf14dc80e517ca2ee6e3afd958b750f264fabd9bb1` and another backend 528/Web/doctor
PASS. No A08 D-level promotion or new live evaluation is implied.

## A09 — Dynamic Criterion/Profile selection

**Given** 시스템 엔지니어링과 다른 domain 문서가 들어온다.  
**When** Criterion Contract 후보를 compile한다.  
**Then** general core와 후보 profile을 source-grounded하게 선택하고, profile 차이가 required evidence/evaluator/authority/exit criterion을 바꾸면 PROFILE_DECISION_REQUIRED/HOLD한다.  
**금지** 모든 project에 SYSTEMS_ENGINEERING_VERIFICATION 자동 적용.  
**현재 판정** D4. 정상 Thread가 versioned `CriterionProfileRouterPort`를 통해 source-grounded profile 후보를 계산한다. 비시스템 근거는 `GENERAL_RND`로 계속하고, specialized profile이 required evidence/evaluator/authority/exit criterion을 바꾸면 criterion compile을 `PROFILE_DECISION_REQUIRED`로 HOLD한다. Explicit profile RPC는 유지하고 모든 project의 systems-engineering 자동 적용을 제거했다. Verification receipt: `bc3709060d66ca4e5862a8032af2a1e7b9184f88a91fff28ba9d908edd2616c7`.

The A09 bounded storage follow-up groups contract/revision/head/projection/audit/receipt writes
and rejects stale canonical or projected revisions. Combined35 normal/public/profile/fault/reopen
regressions and full backend654/Web/doctor passed. A completion contention-fixture failure led to
test-only wait-budget separation with strict outcomes unchanged and focused20 PASS. Final completion
passed654/Web/doctor with receipt `68c06c0012602f32a51684b03d3ff076b7334e0c9d372336adf7ae0ecf8b440f`. See
[A09 storage verification](../../docs/verification/a09-storage-atomicity-20260905.md).
Existing profile-selection D4 and official authority boundaries are unchanged.

## A10 — Unified audit closure

**Given** acquisition, reasoning, execution, outcome, memory와 revision이 한 cycle에서 발생한다.  
**When** Thread 또는 Project를 닫는다.  
**Then** source/connector, evidence, model, action, sandbox, outcome, memory, revision과 closure receipt가 하나의 검증 가능한 DAG와 manifest에 포함된다.  
**And** Connector/Sandbox receipt도 canonical receipt list/bundle/verify/export에서 조회된다.  
**현재 판정** D4 unified foundation. 정상 R2 cycle의 Connector/Sandbox/Evidence/Model/Action/Execution/Outcome/Revision/Memory receipt가 typed canonical DAG/manifest/bundle/verification에 합류하고 trust axes가 분리된다. Corruption·missing-parent INVALID와 local generate/verify·R3 external release separation을 통과했다. Completion receipt: `f317b65bb20a752f102fe8b766ec5277a57b2bc6394e74efb48cf57b47966972`.

The local storage/authority follow-up binds exact snapshot resources, rights and release scope,
preserves public PARTIAL assertions in DAG trust axes, and separates file staging from atomic
final reference creation. Normal-entry/fault/peer-write and legacy integrity-only controls passed,
followed by backend567/Web/doctor and completion receipt
`bdccc7929975af24226c886e0c1862789b05c178a38e6343c2d002d0d638d139`; see
[A10 storage verification](../../docs/verification/a10-storage-authority-20260905.md).
No external release, new live evidence or whole-namespace maturity promotion is claimed.

## A11 — Fail-closed policy integrity

2026-09-08 개발용 규칙 거래·복구 후속은 [현재 검증 기록](../../docs/verification/a11-rule-transaction-final-20260908.md)을 따른다.
정상 CLI/Hook에서 후보 검증→거래→재개/되돌리기→새 preflight를 검증하며, 아래 제품 A11 D4와는 별개다.
현재 감사 영수증이 최종 코드 범위를 확정한다. N07·N08 구현이나 제품 전체 완료로 전이하지 않는다.

**Given** empty connector allowlist, stale policy digest, security ceiling 초과 또는 egress 위반 중 하나가 존재한다.  
**When** acquisition 또는 sandbox preflight가 실행된다.  
**Then** driver/runtime I/O 전에 typed denial이 발생하며 artifact, head, memory와 receipt truth state가 변하지 않는다.  
**And** receipt에는 authoritative policy revision/digest와 denial basis가 남는다.  
**현재 판정** D4. 정상 `project/source/connect`와 `execution/start`에서 authoritative policy revision/digest를 결속하고 empty allowlist·stale digest·security ceiling·egress 위반을 driver/runtime I/O 전에 typed denial로 차단한다. denial 시 artifact/head/memory/truth receipt state 무변경 readback과 atomic Acquisition UoW fault rollback을 통과했다. Verification receipt: `68478e90e38e006972ec2d4424ea2744be9f8b950f069bd7165eb9c234000de2`.

The final A11 workflow-guard follow-up preserves product D4 and checks nested protected paths
plus exact current debt markers in NOW/OCP/maturity. Architecture117 and full backend676/Web/doctor
passed; immutable completion remains pending. See
[final A11 verification](../../docs/verification/a11-final-followup-20260906.md).

## A12 — Field usefulness and workflow reduction

**Given** preregistered cases, fixed thresholds와 기존 도구 조합 baseline이 결과 확인 전에 봉인된다.  
**When** 독립 연구자·검토자가 같은 유형의 과제를 baseline과 THOTH로 수행한다.  
**Then** critical defect recall, decision completeness, active human time, manual tool transition/re-entry와 안전 hard-zero를 측정한다.  
**And** 사전 봉인된 기준을 통과한 결과만 D6로 승격하며 인터뷰 호감·synthetic fixture·내부 개발자 평가는 대체 증거가 아니다.  
**현재 판정** tooling D4 / field result D1·D6 NOT_RUN. Protocol/case/baseline/threshold/hard-zero를 결과 전에 seal하고, keyed pseudonym session, normal RPC method/outcome event, active-time/manual-burden metric, blind score와 privacy-safe export가 제품 경로에 연결됐다. Reseal·raw sensitive metadata·early score·invalid session은 fail closed한다. 모든 export는 external participant/expert session, time saving, WTP를 `NOT_RUN`, `field_validated=false`, `d6_claimed=false`로 강제한다. Tooling verification receipt: `80b2e02133c83499d95e972a236e6135407247a3c2f51e604173fd95e21ba2fa`. 실제 D6 결과는 없음.

## A13 — Authenticated multiplayer attribution

**Given** 두 명 이상의 actor가 같은 project의 서로 다른 role과 scope로 인증된다.  
**When** 독립 Thread/ChangeSet 작업이 동시에 발생한다.  
**Then** actor identity가 session, project, role, revision, receipt에 결속되고 권한 밖 read/write는 I/O 전에 거부된다.  
**And** 동시 변경은 last-write-wins가 아니라 branch/merge 계약을 따르며 routine work는 순차 승인 Inbox가 되지 않는다.  
**현재 판정** D4 local productization. Local HTTP가 credential을 검증하고 token digest만 저장하며 session을 actor/project/role assignment/capability/data scope에 결속한다. Role은 매 요청 ACTIVE 상태와 capability/scope drift를 재검증하고, project/list·protected baseline·revision sibling provenance·receipt correction·operation cancellation은 authenticated identity에 결속된다. 두 인증 actor의 same-parent proposal은 fast-forward+immutable branch로 보존되고 A07 merge를 따른다. Stored Thread workstream은 claim 전에 검증되지만 revision·receipt·operation-read·source/evidence 전반의 cross-resource workstream ownership은 canonical model 부재로 DEFERRED다. Routine work는 approval Inbox를 만들지 않는다. External institutional SSO와 deployed multi-user concurrency는 NOT_RUN. Latest verification receipt: `56d923bddbebf795a557b835ca42fc59f864e54213dce0c5e28d21d8d69d9550`.

The storage follow-up groups local Project/governance mutations and applies conservative stored
Operation origin-permission coverage before claim and access, preserving exact-origin cancellation.
These permissions do not define exact resource ownership. Explicit claim-busy rollback and same-key
retry behavior passed along with normal HTTP/fault/CAS/reopen controls and backend605/Web/doctor.
Completion passed with receipt `b780c8d7c10352635d7998cd97bda90f35efa80801f5b5dde790447ff1147418`;
see [A13 storage verification](../../docs/verification/a13-project-operation-20260905.md).
Wider sharing/backfill semantics and whole-owner debt remain unresolved; no D-level promotion is made.

## 구현 진입 순서

```text
A11 policy integrity
→ A02 autonomous acquisition
→ A03 counter-search
→ A04 R2 closed loop
→ A10 unified receipt
→ A06 full memory
→ A07 semantic merge
→ A08 improvement
→ A09 dynamic profile
→ A13 multiplayer productization
→ A12 D6 field validation
```

A11은 A02가 권한 밖 Connector를 열지 않게 하는 선행 안전조건이다. 첫 제품 vertical slice는 `A11 + A02`다.
