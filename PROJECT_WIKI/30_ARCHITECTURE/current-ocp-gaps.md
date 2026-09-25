---
id: CURRENT-OCP-GAPS
status: ACTIVE
page_role: implementation_gap_projection
authority: ../../research-briefs/CANONICAL_RND_EVIDENCE_HARNESS_DESIGN.md
last_updated: 2026-09-06
---

# Current OCP Gaps

이 페이지는 canonical OCP 설계와 현재 local reference prototype 사이의 구체적인 차이를 기록한다.
Architecture PASS나 RPC parity를 전체 OCP 준수로 해석하지 않는다.

현재 사실과 남은 normal-loop 간극은 [2026-09-06 교정](current-truth-and-next-gap-20260906.md)을 따른다.
마지막 A11 completion은 PASS다. 중첩 경로 오탐은 닫혔지만 읽기 전용 inline interpreter를
mutation으로 분류하는 보수적 경계는 남아 있다. Hook 파일·검사 통과만으로 모든 작업의 실제
Hook 신뢰·실행이나 모든 기능의 의미적 완성을 인증하지 않는다.

## 확인된 준수

- `application -> adapters` 직접 import 0건
- domain/application core의 Hero·ProjectPack·provider 이름 분기 0건
- model, parser, connector, sandbox, Criterion Profile, ProjectPack, migration port/registry 존재
- canonical owner 13개, Acceptance 13개, ratcheted exception 0, migration single head
- 현재 정상 진입점 bounded loop와 G01/G02 이후 backend `361 passed` 두 차례

## OCP-CONNECTOR-001 — CLOSED LOCAL

`ConnectorCapability`가 typed selector contract를 제공하고 `ConnectorRegistry.route`가 exact,
ambiguous, no-match를 판정한다. `ConnectorService`의 selector field와
`GIT/S3/POSTGRES/MCP/LOCAL` 열거는 제거됐다.

목표 계약:

```text
ConnectorCapability가 selector schema·source kind·priority를 제공
→ ConnectorRegistry가 후보를 계산
→ application은 connector 이름을 알지 않고 exact/ambiguous/no-match만 처리
```

Synthetic REST/CUSTOM Connector의 registry-only route, ambiguous/no-match fail-closed, raw
credential 우선 차단, 기존 A02/A03/A11/source 회귀와 첫 full backend `366 passed`를 통과했다.
구조 수정은 기존 A02 D4를 유지하며 credentialed Connector product-loop D5를 뜻하지 않는다.

## OCP-SANDBOX-001 — CLOSED LOCAL

모든 `SandboxPort`가 typed `SandboxCapability`를 제공하며 `SandboxFactoryRegistry`가 adapter ID,
capability, factory와 runtime version을 함께 등록한다. `create_runtime`의 concrete class-name
profile map은 제거됐고 factory가 선언과 다른 capability를 반환하면 fail closed한다.

목표 계약:

```text
SandboxFactoryRegistry가 profile·capability·factory·runtime version을 소유
→ create_runtime은 선택된 registration만 조합
→ 새 Sandbox 추가 시 class-name map과 application core 수정 0
```

Unfamiliar adapter 등록, capability mismatch 거부, A04/A05/A11 회귀, architecture와 첫 full
backend `368 passed`를 통과했다. 구조 수정은 기존 A04 D4를 유지하며 개별 adapter나 production
isolation의 새 D5 증거를 뜻하지 않는다.

## OCP REGRESSION GUARDS — ACTIVE

AST 기반 `check_ocp_extensions.py`가 Connector application route의 kind/selector 열거와
`create_runtime`의 concrete Sandbox class-name routing을 검사한다. 이 검사는 architecture와 새
preflight 양쪽에 포함되며, deliberate violation fixtures와 현재 코드의 positive path를 모두
검증한다. `OCP-HERO-001`은 Hero trace 구현과 같은 노드에서 활성화한다.

추가 enforcement:

- `OCP-REGISTRY-001`: 필수 extension registry가 누락되거나 PARTIAL인데 debt owner가 없으면 실패
- `OCP-FACTORY-001`: IMPLEMENTED factory target이 실제 source symbol이 아니면 실패
- `OCP-CORE-CLOSED-001`: application이 concrete adapter 이름을 import·branch하면 실패
- `OCP-DOC-DRIFT-001`: NOW·maturity·OCP projection이 닫힌 항목을 다시 OPEN으로 쓰거나 debt를 숨기면 실패

Parser/Connector/Sandbox의 factory target은 이제 정확한 module의 concrete registry이며,
기존 `ParserRegistryPort`/`ConnectorRegistryPort`/`SandboxFactoryRegistryPort` signature와
실제 registration/composition 및 runtime consumer에 결속된다. Known-string 검사는 제한적 회귀
방지이며 전체 Python OCP의 완전 정적 증명이 아니다. A11 최종 backend 462/Web/doctor 및 receipt
`3cbf8ae09482a6ac8ec4a6e90f80161ebab9c82fe7d1994755d0403274cc08e5`를 따른다.
Store는 `AtomicUnitOfWorkPort`가 있으나
namespace 전체 원자성은 아직 PARTIAL이다.

## Canonical atomicity debt — OPEN

```text
ATOMICITY DEBT: 10 OPEN
```

현재 owner 13개 중 3개만 `ATOMIC`이고, 9개는 `PARTIAL`, Improvement는
`MATERIALIZED_ONLY`다. 총 10개 non-atomic owner는 각각 debt ID, owning Acceptance와 필요한 fault
injection evidence를 manifest에 가진다. 이 등록은 구현 완료나 예외 승인이 아니라 숨겨지지 않는
다음 작업 목록이다.

## HERO-TRACE-001 — CLOSED LOCAL

Hero는 fresh workspace-scoped `TuiSessionService`에서 `/project`·`/thread` 없이 자연어 한 번으로
진입한다. Project-scoped SQLite operation store에서 TUI method·operation ID·scope digest·state를 읽어
`thread/input` 1회, 수동 semantic RPC 0회와 trace digest를 계산한다.

목표 계약:

```text
Operation journal/CommandBus trace에서 실제 호출 method 관측
→ thread/input 정확히 1회
→ 금지된 수동 semantic RPC 조립 0회
→ manifest는 관측값에서 생성
```

Sealed Hero focused 회귀와 첫 full backend `378 passed`를 통과했다. Architecture의
`OCP-HERO-001`은 해당 manifest 값을 literal로 자기기입하면 실패한다. Fresh live OAuth 시연과 D5
갱신은 별도 실행 증거다.

## 현재 판정

마지막 workflow guard 보완은 중첩 경로 오판과 NOW/OCP/maturity 현재 부채 표시를 검증했다.
Architecture117·full backend676/Web/doctor PASS, 최종 완료 영수증은 pending이다.
상세는 [최종 기록](../../docs/verification/reevaluated-storage-authority-final-20260906.md)을 따른다.

`CORE LAYERING PASS / REGISTRY+FACTORY+CORE-CLOSED GUARDS ACTIVE / ATOMICITY DEBT 10 OPEN`

Connector·Sandbox·Hero OCP 항목은 local code와 regression guard 수준에서 닫혔다. Store와 10개
atomicity debt, operator-grade TUI, 기관 SSO, Field D6, Production 배포와 전체 UI는 별도 gate다.
