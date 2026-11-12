# Security policy

This policy covers the **0.1.x preview line** (first release `v0.1.0-preview`), the **0.2.x beta line** (first beta `v0.2.0-beta.1`) and the latest `main`.
It is not a security certification or a promise of support for older snapshots.

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
- **A web page is not such a program.** A request that carries an Origin is accepted only from the
  page the server itself served and from the web development server (`http://127.0.0.1:5173`,
  `http://localhost:5173`). A page of another local web server or notebook is refused, and the UI
  tells browsers not to draw it inside another site's frame. More local origins can be named in
  `THOTH_ALLOWED_ORIGINS` (comma separated); a non-local origin is never accepted.
- **Hosted review is a separate mode with separate access controls.** Local-mode checks do not
  establish the security of a hosted deployment. See the [documented limitations](docs/KNOWN_LIMITATIONS.md).
- **The workspace contains secrets.** Registered API keys are held in its `model-registry` directory;
  provider sign-in profiles can also hold tokens. The key file and the sign-in profiles are
  closed to other ordinary accounts (on Windows the current user gets full control, and entries
  inherited from the folder and entries for broad groups such as Users and Everyone are removed;
  entries for SYSTEM and Administrators set on the file itself are left; mode 0600 elsewhere);
  the rest of the workspace has the permissions of its folder. Treat the whole workspace and
  backups as sensitive.
  Keep them out of source control, public logs and shared exports. The preview does not promise that
  workspace secret files are encrypted at rest.
- A model suggestion or evidence link is not approval to execute an action. Retain the application's
  source, scope and authorization checks. See [installation and backup guidance](docs/INSTALL.md).

## 한국어 안내

0.1.x 미리보기(첫 릴리즈 `v0.1.0-preview`)와 0.2.x 베타(첫 베타 `v0.2.0-beta.1`), 최신 `main`의 보안 정책입니다. 비공개 취약점 신고는 저장소의
**Security → Report a vulnerability**를 사용하세요. 2026-10-25 기능을 켜고 저장소 설정을 확인했습니다.
신고 화면에 접근할 수 없더라도 민감한 내용을 공개 이슈에 대신 올리지 마세요.
공개 이슈에 취약점 세부 내용·자격 증명·개인 연구 자료를 올리지 마세요. 답변 기한이나 보상은 약속하지 않습니다.

로컬 모드는 같은 컴퓨터의 사용자와 프로그램을 신뢰하며 별도 사용자 인증을 제공하지 않습니다.
Hosted review의 권한 체계와 구분하세요. 작업 공간의 `model-registry`와 로그인 프로필에는 비밀 정보가
포함될 수 있으므로 백업까지 보호해야 합니다. 키 파일과 로그인 프로필은 다른 일반 계정이 읽지 못하게 권한을 좁혀 둡니다(Windows에서는 폴더에서 상속한 항목과 Users·Everyone 같은 넓은 그룹 항목을 지우고 현재 사용자에게 전체 권한을 줍니다. 파일에 직접 걸린 SYSTEM·Administrators 항목은 남깁니다).

웹 페이지는 "같은 컴퓨터의 프로그램"으로 보지 않습니다. Origin이 붙은 요청은 서버가 직접 내준 화면과
웹 개발 서버(`http://127.0.0.1:5173`, `http://localhost:5173`)에서 온 것만 받고, 다른 로컬 웹 서버의
페이지는 거절합니다. 다른 포트의 개발 서버는 `THOTH_ALLOWED_ORIGINS`(쉼표로 구분)에 적습니다. 화면은 다른
사이트의 프레임 안에 그려지지 않습니다.
