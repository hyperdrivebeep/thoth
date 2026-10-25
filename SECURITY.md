# Security policy — draft

This policy is prepared for the **0.1.x preview line on the latest `main`**. It is not an announcement
of a tagged release, a security certification or a promise of support for older snapshots.

## Reporting a vulnerability

Report vulnerabilities through GitHub's private vulnerability reporting:
**Security → Report a vulnerability** on [this repository's security page](https://github.com/hyperdrivebeep/thoth/security).
Do not post vulnerability details, credentials, private research data or exploitable examples in public issues.

Private vulnerability reporting was enabled and its repository setting was verified on 2026-10-25.
If the private form is unavailable to you, do not substitute a public issue containing sensitive
details. No alternate private contact has been designated here.

Include the affected revision, prerequisites, impact and a
minimal reproduction using synthetic data. Remove credentials and private documents from any logs.
This preview does not promise a response deadline or a reward.

## Security boundaries

- **Local mode trusts the user and programs on the same computer.** Loopback binding and local
  Host/Origin checks are not authentication. Another local program can make permitted requests.
  Do not expose the local server as a shared or internet-facing service.
- **Hosted review is a separate mode with separate access controls.** Local-mode checks do not
  establish the security of a hosted deployment. See the [documented limitations](docs/KNOWN_LIMITATIONS.md).
- **The workspace contains secrets.** Registered API keys are held in its `model-registry` directory;
  provider sign-in profiles can also hold tokens. Treat the whole workspace and backups as sensitive.
  Keep them out of source control, public logs and shared exports. The preview does not promise that
  workspace secret files are encrypted at rest.
- A model suggestion or evidence link is not approval to execute an action. Retain the application's
  source, scope and authorization checks. See [installation and backup guidance](docs/INSTALL.md).

## 한국어 안내

최신 `main`의 0.1.x 미리보기를 위한 보안 정책 초안입니다. 비공개 취약점 신고는 저장소의
**Security → Report a vulnerability**를 사용하세요. 2026-10-25 기능을 켜고 저장소 설정을 확인했습니다.
신고 화면에 접근할 수 없더라도 민감한 내용을 공개 이슈에 대신 올리지 마세요.
공개 이슈에 취약점 세부 내용·자격 증명·개인 연구 자료를 올리지 마세요. 답변 기한이나 보상은 약속하지 않습니다.

로컬 모드는 같은 컴퓨터의 사용자와 프로그램을 신뢰하며 별도 사용자 인증을 제공하지 않습니다.
Hosted review의 권한 체계와 구분하세요. 작업 공간의 `model-registry`와 로그인 프로필에는 비밀 정보가
포함될 수 있으므로 백업까지 보호해야 합니다.
