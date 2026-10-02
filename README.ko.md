<p align="center">
  <img src="docs/brand/thoth-readme-banner.svg" alt="THOTH — 근거, 판단, 변경 이력" width="960">
</p>

<p align="center">
  <strong>근거를 따라, 판단의 변화를 남기다.</strong><br>
  Follow the evidence. Keep the history of your judgment.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-4C90F0?style=flat-square" alt="MIT 라이선스"></a>
  <a href="#현재-상태"><img src="https://img.shields.io/badge/status-experimental_preview-E7B96E?style=flat-square" alt="실험적 공개 버전"></a>
  <a href="docs/INSTALL.md"><img src="https://img.shields.io/badge/runs-locally-26384C?style=flat-square" alt="로컬 실행"></a>
</p>

<p align="center">
  <a href="#빠른-시작">시작하기</a> ·
  <a href="#무엇을-할-수-있나요">주요 기능</a> ·
  <a href="docs/VERIFICATION.md">검증 기록</a> ·
  <a href="CONTRIBUTING.md">기여하기</a><br>
  <a href="README.md">English</a> · <strong>한국어</strong>
</p>

---

자료가 바뀌고, 결과가 수정됩니다. 함께 일하는 사람은 그 이유를 알아야 합니다.

**THOTH는 연구 질문, 원문 근거, 경쟁 가설, 저장된 결과를 연결하는 로컬 AI 연구 워크벤치입니다.**
판단을 뒷받침하는 근거와 아직 풀리지 않은 문제를 살펴보고, 저장된 두 결과를 비교하며 다음 행동을 정합니다.

<p align="center">
  <img src="docs/brand/thoth-ibis-hero.png" alt="초승달 아래 파피루스에 기록하는 따오기 머리의 토트. THOTH의 남색과 파랑을 사용한 브랜드 일러스트." width="960">
  <br><sub>THOTH 브랜드 일러스트 — 기록의 신 토트를 연구 워크벤치의 이미지로 표현했습니다.</sub>
</p>

> [!NOTE]
> **개인·로컬 사용을 위한 실험적 소스 공개본입니다.** UI는 한국어 중심입니다.
> 집중 기능 검사는 통과했지만 최신 전체 회귀 검사는 통과하지 못했습니다. 아래 검증 범위를 확인하세요.
> **2026-09-29 반영:** 모델별 전용 인증과 재시작·저장 복구 수정을 포함합니다. 실제 모델 분석과 현재 소스의 전체 검사 통과는 미확인이고, Claude 로그인은 공식 Claude Code 실행 파일을 거치며 실계정으로는 아직 확인하지 않았습니다.
> 결과를 사용하기 전에 [현재 상태와 한계](#현재-상태)를 확인하세요.

## 무엇을 할 수 있나요

| 근거를 따라갑니다 | 판단을 검토합니다 | 변화를 남깁니다 |
| :--- | :--- | :--- |
| 문서를 연결하고 원문 위치, 자료 버전, 시점의 적격성을 살펴봅니다. | 답변과 함께 경쟁 가설, 부족한 근거, 다음 행동을 검토합니다. | 저장된 두 결과를 정확히 골라 비교합니다. 지원되는 복원은 기존 기록을 지우지 않고 새 버전으로 남깁니다. |

**이전 결과를 보존하면서 질문을 이어갑니다.** 작업이 실행 중이면 새 입력을 대기시킬 수 있으며, 재전송과 복구에서도 요청의 정체성을 유지합니다.

**미해결 상태를 드러냅니다.** 처리가 끝나도 답변은 부분적일 수 있고 판단은 보류될 수 있습니다. 처리 완료와 근거 충족을 구분합니다.

**사용할 연결을 직접 고릅니다.** 로컬 작업 공간에서 지원되는 로그인 또는 API 키를 설정하고 모델과 추론 수준을 선택합니다. 저장된 선택을 유지하며, 지원되지 않는 경로는 명시합니다. 저장된 결과나 비교를 읽는 것만으로 새 모델 호출이 시작되지는 않습니다.

## 다시 살펴볼 수 있는 연구 흐름

| 단계 | THOTH에서 하는 일 |
| :--- | :--- |
| **1 · 질문** | 연구 질문을 만들고 관련 문서를 연결합니다. |
| **2 · 검토** | 답변의 원문 근거를 확인하고 경쟁 가설과 부족한 정보를 살펴봅니다. |
| **3 · 이어가기** | 자료나 후속 질문을 추가합니다. 실행 중인 작업 뒤에 입력을 대기시킬 수 있습니다. |
| **4 · 비교** | 저장된 두 결과를 골라 무엇이 달라졌는지 확인하고, 필요하면 이력을 다시 살펴봅니다. |

## 빠른 시작

**Windows · 소스 체크아웃 · Python 3.12 또는 3.13 · [uv](https://docs.astral.sh/uv/) · Node.js · pnpm**

Git으로 저장소를 내려받는다면 다음 명령을 실행합니다.

```powershell
git clone https://github.com/hyperdrivebeep/thoth.git
cd thoth
```

GitHub의 **Code → Download ZIP**으로 받았다면 압축을 풀고 `thoth-main` 폴더에서 PowerShell을
엽니다. 압축을 푼 상위 폴더에 있다면 `cd thoth-main`을 실행하세요.

**1. Python을 확인하고 잠금 파일에 맞춰 의존성을 설치합니다.** 저장소 폴더에서 실행하세요.

```powershell
$thothPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
Test-Path -LiteralPath $thothPython
& $thothPython -m venv --copies --without-pip .venv
uv sync --python .\.venv\Scripts\python.exe --no-managed-python --no-python-downloads --frozen --extra dev --dev
pnpm.cmd install --frozen-lockfile
```

`Test-Path`가 `False`를 출력하면 공식 Python을 먼저 설치하세요. 서명 확인을 포함한 절차는
[Windows Python 준비](docs/INSTALL.md#prepare-python-on-windows)에 있습니다.

Python을 다른 경로에 설치했다면 위 `$thothPython`을 설치한 `python.exe` 경로로 바꿉니다.

**2. 첫 번째 터미널에서 API를 시작합니다.**

```powershell
.\.venv\Scripts\python.exe -m thoth.cli workspace
.\.venv\Scripts\python.exe -m thoth.cli serve --port 8765
```

**3. 두 번째 터미널에서** 같은 `thoth` 또는 `thoth-main` 폴더로 이동한 뒤 웹 화면을 시작합니다.

```powershell
pnpm.cmd --dir apps/web run dev
```

**4. 앱 안에서 모델을 연결합니다.** **<http://127.0.0.1:5173/>**을 열면 처음 설정 화면에 ChatGPT·Claude·xAI가
표시됩니다. 원하는 서비스의 로그인 버튼을 눌러 브라우저에서 로그인을 마치면 THOTH가 그 계정의 모델 목록을
불러옵니다. 본인의 API 키로 연결할 수도 있으며, 이 단계에는 터미널이 필요 없습니다.
필요한 도구(ChatGPT의 Codex CLI, Claude의 Claude Code)가 없으면 카드에 자동 설치 버튼이 나옵니다.
Node.js가 필요하고 `%LOCALAPPDATA%\THOTH\tools`에만 설치하며, 전역 설치나 다른 프로그램의 로그인은 건드리지 않습니다.
수동 명령은 [docs/INSTALL.md](docs/INSTALL.md)에 있습니다. 계정 로그인은 그 계정의 다른 앱과 사용량 한도를 함께 쓰고,
실제 호출에는 해당 서비스의 사용량이 소모될 수 있습니다.

새 Windows 설치의 연구 데이터는 소스 폴더와 별개인 `%LOCALAPPDATA%\THOTH`에 저장됩니다.
이전 버전을 사용했다면 `serve --workspace "C:\기존경로\.thoth-local" --port 8765`로
원래 데이터 폴더를 지정하세요. 시작할 때 실제 저장 위치를 표시하며, 기존 자료를 자동 이동하지 않습니다.

로그인은 THOTH 전용 프로필을 사용하며, 다른 프로그램의 기존 로그인 정보는 가져오지 않습니다.
Claude 로그인은 그 프로필에서 공식 Claude Code를 실행하고, xAI는 기기 코드 로그인을 사용합니다.
둘 다 가짜 제공자·가짜 실행 파일로만 확인했고(xAI는 Chrome/RPC 흐름도 통과), 실계정 검증은 아직 남아 있습니다.
[연결 조건과 재시작 안내](docs/INSTALL.md)를 확인하세요.

API는 로컬 루프백 주소에 바인딩되고, Vite는 API 요청을 8765 포트로 전달합니다. 소스와 migration 디렉터리를 함께 사용해야 하며, 이 공개본은 독립 wheel 설치를 지원하지 않습니다.

<details>
<summary><strong>설치 환경과 선택적 연동</strong></summary>

격리된 Windows 검증에는 Python 3.13.15, Node 24.19.0, pnpm 12.6.0, uv 0.12.5를 사용했습니다.
기본 PDF 처리는 pypdf를 사용합니다. 구조화 PDF, 브라우저 수집, 추가 커넥터, 관리형 샌드박스는 별도 의존성과 실행 조건이 있습니다.

기본 연결은 OMO나 Codex Desktop 인증 정보를 가져오지 않습니다. 실험적인 Codex 연결은 자체 프로필로 로그인·토큰 갱신·모델 목록 조회를 수행합니다. 계정 설정은 저장소에 배포하지 않으며, 로그인 확인만으로 실제 모델 응답 성공을 보장하지 않습니다.

자세한 내용은 [설치와 인증 안내](docs/INSTALL.md)를 확인하세요.

</details>

## 현재 상태

이 저장소는 **실험적 참조 구현**이며, 다음 검증 범위 안에서 상태를 설명합니다.

| 로컬에서 확인한 항목 | 검증 범위 |
| :--- | :--- |
| 입력 대기열, 입력 버전 검사, 정확한 두 결과 비교 | 선택한 통합 테스트 4건 통과. 변경되지 않은 비교 UI는 기존 Chrome 검증 기록을 유지합니다. |
| 새 환경의 frozen 설치, doctor, 웹 build | 기록된 Windows 환경에서 통과했습니다. |
| 합성 근거 데이터 UI | 집중 웹 테스트 36건을 통과했습니다. |
| 첫 실행 Chrome/API 확인 | 인증 정보나 모델 호출 없이 통과했습니다. 모델 미연결은 예상된 초기 상태입니다. |
| 2026-09-28 인증·재시작 r2 | 합성 제공자를 사용한 독립 Chrome/RPC 검증을 부분 인수했습니다. 저장 결과 5개를 별도 프로세스에서 모델 재호출 없이 다시 열었습니다. 진단 중 오류와 미실행 장면은 검증 문서에 남겼습니다. |
| 공식 Python 복구 후 연결·재시작 Web 인수 | Web 247건·doctor 8항목 통과. 실제 Chrome/RPC와 가짜 제공자로 초기 설정·저장 결과·초안·작업 공간 분리를 확인했습니다. 실제 OAuth와 PC 재부팅은 미검증입니다. |

**최신 전체 회귀 검사는 통과하지 못했습니다.** 집중 기능 검사와 별개이며, 정확한 실패와 환경 차단 범위는 검증 문서에 남겼습니다. 재배포 권리를 확인하지 못한 외부 원문 자료는 공개본에 포함하지 않습니다.

이 검증으로 실제 모델의 답변 품질, 여러 runtime을 사용하는 Hosted 운영 준비, 폭넓은 플랫폼 호환성, 실제 협업자의 시간 절감 효과가 입증된 것은 아닙니다.

[검증 기록](docs/VERIFICATION.md) · [미해결 테스트 목록](docs/KNOWN_TEST_FAILURES.json) · [공개 파일 범위](docs/PACKAGING.md)

## 내부를 살펴보기

| 영역 | 위치 |
| :--- | :--- |
| Python 백엔드 | [`src/thoth`](src/thoth) — domain → ports → application → adapters, 조립은 `apps`. |
| React / TypeScript UI | [`apps/web`](apps/web) |
| 정본 데이터와 변경 이력 | SQLite, 버전별 migration은 [`migrations`](migrations). |
| 로고·일러스트·색상·소개 문구 | [`docs/brand`](docs/brand) |

화면의 요약은 정본 데이터나 권한 검사를 대신하지 않습니다. 재현에는 합성 자료와 격리된 작업 공간을 사용하며, 자료 시점·범위·현재성·권한 검사를 유지합니다. 변경을 제안하기 전에 [기여 안내](CONTRIBUTING.md)를 읽어주세요.

## 라이선스

이 공개본의 자체 코드, 문서, THOTH 브랜드 자산, 자체 합성 fixture는 **[MIT License](LICENSE)**로 제공합니다. Copyright 2026 THOTH.
의존성에는 각자의 라이선스가 적용됩니다. [외부 의존성·자산 고지](THIRD_PARTY_NOTICES.md)를 확인하세요.

<p align="center">
  <img src="docs/brand/thoth-mark.svg" alt="근거를 연결하는 THOTH 심볼" width="40"><br>
  <sub>Evidence. Judgment. Revision.</sub><br>
  <a href="https://github.com/hyperdrivebeep">Built by Hyperdrive</a>
</p>
