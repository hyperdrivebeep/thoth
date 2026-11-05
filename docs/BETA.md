# THOTH beta guide

THOTH is a local, AI-assisted research workbench: you ask a question about your own documents,
follow each answer back to the evidence, and keep the history of how a judgment changed.
This page is for people who agreed to try the **beta** and tell us what breaks or confuses them.

한국어: 베타 테스트에 참여해 주셔서 감사합니다. 설치하고, 아래 다섯 가지 흐름을 한 번씩 써 보고,
막히거나 이상한 점을 알려 주세요. 한국어 요약은 [맨 아래](#한국어-요약)에 있습니다.

## Who this is for

- You use **Windows** and are comfortable running a few PowerShell commands (a source checkout,
  not an installer; see [INSTALL.md](INSTALL.md)).
- You can bring a ChatGPT/Codex, Claude or xAI account, or your own provider API key.
- You have a small set of documents you may share with that model provider (text, Markdown, HTML
  or PDF). Use material you are allowed to use; see [Privacy and cost](#privacy-and-cost).

This is still an experimental preview. A beta tests whether the product is usable; it does not
claim that answers are correct or useful on your data.

## Install

1. Follow the [installation guide](INSTALL.md) or the [README quick start](../README.md#quick-start).
   Use Python 3.12 or 3.13, uv, Node.js and pnpm.
2. If you used the earlier preview, run the locked install again (`uv sync ... --frozen` and
   `pnpm.cmd install --frozen-lockfile`): the beta adds one required Python package.
3. Back up your workspace before opening it with the beta. Opening an existing workspace runs
   database migrations, and THOTH has no downgrade command (see [Upgrading](#upgrading)).
4. Check what you installed: `.\.venv\Scripts\python.exe -m thoth.cli version`. The beta prints
   `THOTH 0.2.0b1` (the release is named `v0.2.0-beta.1`; Python writes the same version as
   `0.2.0b1`). The earlier preview prints `0.1.0`. Put that line in any report.

**Optional: local retrieval preparation.** Settings has a card, *Local retrieval preparation*
(로컬 검색 준비). It is off by default and needs an extra install:

```powershell
uv sync --python .\.venv\Scripts\python.exe --no-managed-python --no-python-downloads --frozen --extra dev --dev --extra local-retrieval
```

It adds about 110 MB to the Python environment and about 124 MB of model files per workspace
(downloaded from huggingface.co or imported by hand from the card). You do not need it for the
five flows below. See [Known limits](#known-limits-in-this-beta) before enabling it.

## First run and connecting a model

Start the API and the web interface as in the README, then open <http://127.0.0.1:5173/>. The
first-run screen lists ChatGPT, Claude and xAI sign-in buttons and an API-key option. Finish the
sign-in in your browser; THOTH then loads that account's model list. A started sign-in is not a
finished sign-in: refresh the connection status afterwards. If a helper program is missing, the
card offers an automatic install (it needs Node.js).

THOTH keeps its own sign-in profile and does not read other programs' credentials. Real-account
sign-in for Claude and xAI has been tested only with fake providers, so treat those two as the
least proven.

## Five flows to try

Try them in this order with a throw-away project. After each one, note what you expected and
what happened.

| # | Flow | What to do | What to look for |
| --- | --- | --- | --- |
| 1 | Create a project | Choose **New project** (새 프로젝트) and give it a name. | The project appears in the sidebar and opens. |
| 2 | Connect sources | In the input box choose **Connect sources** (자료 연결) and add two or three files. | Each file is accepted, appears under the project, and its text can be opened. Tell us about files that fail or look garbled. |
| 3 | Ask a question | Ask something your documents can answer. Pick the response language you want for this question. | Progress is shown while the investigation runs. A usage line under the saved result shows calls and tokens. |
| 4 | Read the result | Open the answer, its evidence references, the hypotheses and tests, and the research history. | Every claim points to a source position you can open. Note anything you cannot trace back, anything that reads as overconfident, and anything unclear. Changing only the display language should not start a model call. |
| 5 | Archive, then delete permanently | In the project menu choose **Archive project** (프로젝트 보관). It only archives. Open **View archived projects** (보관한 프로젝트 보기), choose **Delete permanently** (영구 삭제), and type the project name when asked. | Archiving can be undone with **Restore** (되살리기). Permanent deletion cannot. Check that the project and its conversations disappear and that other projects are untouched. |

Optional sixth flow: switch the display language and the response language in Settings, then ask a
second question in the other language in the same conversation.

## Known limits in this beta

- **Search experiment is off by default and did not pass.** The experimental retrieval rule that
  uses the local index was tested on a held-out set of 30 cases and failed our promotion criteria
  (it helped when many documents were mixed, but not inside one project's own documents, and it was
  too slow). So it stays an experiment. Preparing the index does not change how evidence is chosen for
  your questions.
- **Local retrieval parts are optional.** They need the extra install above and a model download
  of about 124 MB per workspace. Passages longer than 512 tokens are embedded from their first 512
  tokens only, so evidence deep inside a very long passage is not reflected in the index.
  Which passages were cut is recorded per stored vector. If you used local indexing before this
  release, the project card says the index must be rebuilt; choose 'Prepare index' again. The model
  files are not downloaded again.
- **Permanent deletion keeps shared files.** Identical files are stored once, so a file that another
  project also uses stays on disk. Files only this project used are removed. There is no tool yet to
  clean up files no project uses.
- **Permanent deletion can be refused.** Only archived projects can be deleted. It is refused while
  work in the project is still running, and in the hosted review environment. If THOTH was closed
  abnormally while a job was running, its leftover "running" record stays as it is (starting THOTH
  again changes nothing), but deleting the project marks it as failed first when its owning process
  is confirmed gone, so it no longer blocks the deletion. A record whose owning process cannot be
  confirmed as gone (it has no owner record, or was written on another computer or before a restart
  of the machine) is left as it is and can still block deletion. Deletion cannot be undone and
  nothing is exported for you first.
- **Windows only, source checkout only.** There is no installer and no standalone package.
- **The Chinese screen is machine-translated and has not been reviewed.** Simplified Chinese
  (中文(简体)) can be chosen as the display language in Settings. Its wording was produced by machine
  translation from the Korean and English text and no native speaker has checked it, so some phrases
  may be stiff or wrong. Messages that the server writes itself (for example the workspace-location
  notice) appear in English on this screen. The display language and the answer language are
  separate settings.
- **The Japanese screen is machine-translated and has not been reviewed.** Japanese (日本語) can be
  chosen as the display language and as the answer language in Settings. Its wording was produced by
  machine translation from the Korean and English text and no native speaker has checked it, so some
  phrases may be stiff or wrong. Messages that the server writes itself appear in English on this
  screen.
- **Model accounts.** The ChatGPT/Codex bridge is experimental and uses a private endpoint. Claude
  and xAI real-account use is unverified.
- **Small display issues.** On Korean Windows a backslash can show as ₩ in some code or command
  boxes. The text under the model chooser next to the input box can be clipped.
- **Answers are not validated.** Recorded runs used synthetic data. The full list is in
  [Known limitations](KNOWN_LIMITATIONS.md) and the [verification record](VERIFICATION.md).

## Privacy and cost

- Your workspace (projects, documents, results, settings) stays on your computer, in
  `%LOCALAPPDATA%\THOTH` unless you chose another folder. Sign-in tokens are stored in that
  workspace too. Do not commit or share it.
- When you ask a question, THOTH sends the relevant parts of your documents and your question to the
  model account you connected. Use only material you are allowed to send there.
- Each investigation uses model calls on **your** account. In recorded synthetic runs this was
  about 8–9 calls and 330,000–450,000 tokens per investigation, mostly input. Sign-in uses your
  subscription allowance; API keys are billed by the provider. See
  [model usage and cost](INSTALL.md#model-usage-and-cost). Reading a saved result or changing the
  display language does not call a model; sending a question does.
- Never paste credentials, personal data or screenshots showing them into a bug report.

## Upgrading

- Back up the whole workspace folder first, while THOTH is stopped.
- The beta adds two database migrations: one for the local retrieval index tables and one for the
  record of permanently deleted projects (`8e3b5f1a7c42`). They only add tables. Opening a workspace
  with the beta applies them automatically. THOTH has no downgrade command and an older version has
  not been tested against a migrated workspace, so keep the backup if you want to go back.
- `markdown-it-py` is now a required dependency; rerun the locked install.

## Reporting what you find

Open a **Beta feedback** issue: <https://github.com/hyperdrivebeep/thoth/issues/new/choose>.
Tell us what you were doing, what you expected, what happened, and attach a screenshot. Add the
output of `thoth.cli version` and your Windows version. Remove or blur anything private first: do
not attach your documents, sign-in details or workspace files.

For a security problem, do not open a public issue. Use **Security → Report a vulnerability** on the
repository and read the [security policy](../SECURITY.md).

## 한국어 요약

- **대상**: Windows에서 PowerShell 명령을 몇 줄 실행할 수 있는 분. 설치 파일이 아니라 소스를
  내려받아 설치합니다([설치 안내](INSTALL.md)). ChatGPT/Codex·Claude·xAI 계정이나 직접 받은 API 키가
  필요합니다.
- **설치**: 설치 안내를 따르고, 이전 미리보기를 썼다면 잠금 설치를 다시 실행하세요(필수 패키지가
  하나 늘었습니다). 작업 공간을 열기 전에 폴더를 통째로 백업하세요. 베타는 데이터베이스 표를 두 개
  추가하고, 되돌리는 명령이 없으니 백업을 보관하세요. `python -m thoth.cli version`으로 버전을
  확인해 신고에 적어 주세요.
- **시험할 흐름 5개**: 새 프로젝트 만들기 → 자료 연결 → 질문 → 결과 읽기(근거를 따라가 보기) →
  프로젝트 보관 후 "보관한 프로젝트 보기"에서 영구 삭제(프로젝트 이름 입력). 영구 삭제는
  되돌릴 수 없습니다.
- **알려진 한계**: 로컬 검색 준비는 기본 꺼짐이고 실험이며, 보류 평가에서 기준을 넘지 못했습니다.
  부품은 선택 설치(약 110MB + 모델 약 124MB)입니다. 영구 삭제해도 다른 프로젝트와 공유하는 파일은
  남고, 작업이 실행 중이면 삭제가 거절될 수 있습니다. 비정상 종료로 남은 실행 기록은 THOTH를 다시
  시작해도 그대로 남지만, 그 프로젝트를 삭제할 때 맡았던 프로세스가 사라진 것이 확인되면 먼저 실패로
  정리해서 삭제를 막지 않습니다. 맡았던 프로세스가 사라졌는지 확인할 수 없는 기록(주인 기록이 없거나
  다른 컴퓨터·재부팅 전에 남은 것)은 그대로 남아 삭제를 막을 수 있습니다.
  한국어 Windows에서 일부 코드 상자의 역슬래시가 ₩로 보일 수 있습니다.
  중국어(간체) 화면은 기계 번역이며 검수되지 않았습니다.
  일본어 화면도 기계 번역이며 검수되지 않았습니다.
- **개인정보·비용**: 질문하면 자료의 관련 부분이 연결한 모델 계정으로 전송되고 그 계정의 사용량을
  씁니다(합성 자료 기준 조사 한 번에 호출 8~9회, 토큰 33만~45만 개). 허락받은 자료만 쓰고,
  신고에는 개인 자료·로그인 정보·작업 공간 파일을 붙이지 마세요.
- **신고**: [Beta feedback 이슈](https://github.com/hyperdrivebeep/thoth/issues/new/choose)에
  하던 일, 기대한 결과, 실제 결과, 화면 캡처, 버전·OS를 적어 주세요. 보안 문제는 공개 이슈 대신
  **Security → Report a vulnerability**로 알려 주세요.

