# THOTH

A local research workbench that keeps questions, evidence, judgments and changes connected.

THOTH helps you inspect what supports a research result, compare two recorded results, and
continue work without losing the earlier result. It is an experimental reference implementation.

## Capabilities

- Connect documents and inspect source-located evidence, source versions and time eligibility.
- Follow a question through its inputs, research activity, partial answers and next actions.
- Queue new input behind running work, with request identity preserved across retries and recovery.
- Compare two exact stored results using a short summary and expandable technical changes.
- Inspect immutable history and restore supported typed content as a new revision.
- Configure supported provider login or an API key in the local workspace. Saved model and
  reasoning choices are preserved; unavailable routes are shown explicitly.

Completion and evidence quality are separate. A completed operation can contain a partial answer
or a held judgment. Reading a stored result or comparison does not start another model call.

## Quick start — Windows source checkout

Requirements: Python 3.12 or 3.13, [uv](https://docs.astral.sh/uv/), Node.js and pnpm.
The isolated Windows check used Python 3.13.15, Node 24.19.0, pnpm 12.6.0 and uv 0.12.5.
Use the source checkout: a standalone wheel without the migration tree is not supported here.

```powershell
uv sync --frozen --extra dev --dev
pnpm install --frozen-lockfile
```

Start the API, then start the Web interface in another terminal:

```powershell
.\.venv\Scripts\thoth.exe serve --workspace .\.thoth-local --port 8765
```

```powershell
pnpm --dir apps/web run dev
```

Open <http://127.0.0.1:5173/>. The API binds to loopback and Vite proxies API calls to port 8765.
In setup/settings, choose a supported login route or add your own provider key, then choose a
model before starting research. Provider use may consume that provider's quota.

The base PDF path uses pypdf. Structured PDF, browser, connector and managed sandbox integrations
have optional dependencies and prerequisites. See [installation](docs/INSTALL.md).

## Status

Input queue and exact-result comparison have local, source-bound focused acceptance. The minimal
feature closeout passed four selected integration tests; its unchanged comparison UI retains the
recorded local Chrome acceptance. These checks used controlled inputs and do not establish
real-provider answer quality or a complete regression pass.

There are **25 known unresolved source-tree regression cases**, kept separate from feature
closeout. They include fixture eligibility and other contract/behavior cases; not every cause is
established. The public source package may also omit legacy assets whose redistribution rights
were not established. See [verification and known limits](docs/VERIFICATION.md), the exact
[known-failure list](docs/KNOWN_TEST_FAILURES.json), and [packaging](docs/PACKAGING.md).

Hosted multi-runtime operation, real collaborator time savings and broad platform portability
are not established by these checks. This preview targets personal/local use with a Korean-first UI.

A clean candidate install, doctor, 36 focused Web tests, Web build and first-run Chrome/API smoke
passed. The smoke used no account credentials or model calls; an unconfigured model was expected.

## Architecture and contributions

The Python backend follows domain -> ports -> application -> adapters, composed under
`src/thoth/apps`. The React/TypeScript UI is under `apps/web`. SQLite stores canonical records and
versioned history; UI summaries do not replace those records or authorization.

Use synthetic data and an isolated workspace for reproductions. Preserve source-time, scope,
currentness and authorization checks. See [contributor notes](CONTRIBUTING.md).

## License and publication

The original code, documentation and THOTH-authored synthetic fixtures in this source snapshot
are licensed under the **MIT License**. See [LICENSE](LICENSE), copyright 2026 THOTH.
Third-party dependencies retain their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The reviewed MIT source preview is available at [hyperdrivebeep/thoth-public](https://github.com/hyperdrivebeep/thoth-public).
This source release does not update the separately managed hosted service.
