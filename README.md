<p align="center">
  <img src="docs/brand/thoth-readme-banner.svg" alt="THOTH — Evidence. Judgment. Revision." width="960">
</p>

<p align="center">
  <strong>Follow the evidence. Keep the history of your judgment.</strong><br>
  근거를 따라, 판단의 변화를 남기다.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-4C90F0?style=flat-square" alt="License: MIT"></a>
  <a href="#status"><img src="https://img.shields.io/badge/status-experimental_preview-E7B96E?style=flat-square" alt="Status: experimental preview"></a>
  <a href="docs/INSTALL.md"><img src="https://img.shields.io/badge/runs-locally-26384C?style=flat-square" alt="Runs locally"></a>
</p>

<p align="center">
  <a href="#quick-start">Get started</a> ·
  <a href="#what-you-can-do">Features</a> ·
  <a href="docs/VERIFICATION.md">Verification</a> ·
  <a href="CONTRIBUTING.md">Contribute</a><br>
  <strong>English</strong> · <a href="README.ko.md">한국어</a>
</p>

---

Your documents change. A result gets revised. A colleague needs to understand why.

**THOTH is a local, AI-assisted research workbench that keeps the question, source evidence,
competing explanations and recorded results connected.** Inspect what supports a judgment,
see what remains unresolved, and compare two saved results before deciding what comes next.

<p align="center">
  <img src="docs/brand/thoth-ibis-hero.png" alt="The ibis-headed Thoth records on papyrus beneath a crescent moon, in the THOTH navy and blue palette." width="960">
  <br><sub>Original THOTH brand illustration — the Egyptian scribe, reimagined for a research workbench.</sub>
</p>

> [!NOTE]
> **Experimental source preview for personal/local use.** The interface is Korean-first.
> Focused feature and installation checks passed; 25 source-tree regression cases remain unresolved.
> See [status and limits](#status) before relying on a result.

## What you can do

| Follow the evidence | Inspect the judgment | Keep the changes |
| :--- | :--- | :--- |
| Connect documents and return to source-located evidence, versions and time eligibility. | Review an answer alongside competing explanations, missing evidence and next actions. | Compare two exact saved results and inspect immutable history. Supported restores create a new revision. |

**Continue a question without losing its earlier result.** Queue new input behind running work;
request identity is preserved across retries and recovery.

**Keep unresolved work visible.** A completed operation can still contain a partial answer or a
held judgment. Completion is not a claim that the evidence is sufficient.

**Choose your own connection.** Configure a supported provider login or API key in your local
workspace, then select the model and reasoning setting. Saved choices are preserved; unavailable
routes are shown explicitly. Reading a saved result or comparison does not start a model call.

## A research loop you can revisit

| Step | In THOTH |
| :--- | :--- |
| **1 · Ask** | Start with a research question and connect the relevant documents. |
| **2 · Inspect** | Follow the answer back to evidence; review competing explanations and gaps. |
| **3 · Continue** | Add information or a follow-up question. Input can wait behind running work. |
| **4 · Compare** | Select two saved results to inspect what changed. Revisit history when needed. |

## Quick start

**Windows · source checkout · Python 3.12 or 3.13 · [uv](https://docs.astral.sh/uv/) · Node.js · pnpm**

Clone the repository and install the locked dependencies:

```powershell
git clone https://github.com/hyperdrivebeep/thoth.git
cd thoth
uv sync --frozen --extra dev --dev
pnpm install --frozen-lockfile
```

Start the API in one terminal:

```powershell
.\.venv\Scripts\thoth.exe serve --workspace .\.thoth-local --port 8765
```

Start the Web interface in a second terminal, from the same repository:

```powershell
pnpm --dir apps/web run dev
```

Open **<http://127.0.0.1:5173/>**. In setup/settings, select a supported login route or enter your
own provider key, then choose a model before starting research. Provider use may consume its quota.

If the backend runs in WSL, complete Codex device login in that WSL terminal with
`codex login --device-auth`. If login is confirmed but no model appears, check the model catalog
path in the [installation and credentials guide](docs/INSTALL.md) before repeating login.

The API binds to loopback; Vite proxies API calls to port 8765. Keep the migration tree with the
source checkout: a standalone wheel is not supported by this preview.

<details>
<summary><strong>Installation details and optional integrations</strong></summary>

The isolated Windows check used Python 3.13.15, Node 24.19.0, pnpm 12.6.0 and uv 0.12.5.
The basic PDF path uses pypdf. Structured PDF parsing, browser acquisition, additional connectors
and managed sandboxes have optional dependencies and prerequisites.

The default connection does not import OMO credentials. The optional official Codex catalog may
read the CLI's local configuration/cache when present. No account configuration is distributed here.

See [installation and credentials](docs/INSTALL.md).

</details>

## Status

This is an **experimental reference implementation**, with evidence scoped to the checks below.

| Checked locally | Boundary |
| :--- | :--- |
| Input queue, strict input-version checking and exact-result comparison | Four selected integration tests passed; unchanged comparison UI retains recorded Chrome acceptance. |
| Fresh frozen installation, doctor and Web build | Passed on the recorded Windows environment. |
| Synthetic evidence interface | 36 focused Web tests passed. |
| First-run Chrome/API smoke | Passed with no credentials or model calls. An unconfigured model was expected. |

**25 source-tree regression cases remain unresolved.** These are separate from the focused feature
checks. No complete current-source regression pass is claimed. Some legacy assets are also omitted
because their redistribution rights were not established.

Real-provider answer quality, hosted multi-runtime readiness, broad platform portability and
actual collaborator time savings have not been established by these checks.

[Verification record](docs/VERIFICATION.md) · [Exact known failures](docs/KNOWN_TEST_FAILURES.json) ·
[Package boundary](docs/PACKAGING.md)

## Built to be inspected

| Area | Location |
| :--- | :--- |
| Python backend | [`src/thoth`](src/thoth) — domain → ports → application → adapters; composition under `apps`. |
| React / TypeScript interface | [`apps/web`](apps/web) |
| Canonical records and versioned history | SQLite, with versioned migrations in [`migrations`](migrations). |
| Brand assets and writing style | [`docs/brand`](docs/brand) |

UI summaries do not replace canonical records or authorization. Use synthetic data and an isolated
workspace for reproductions. Contributions should preserve source-time, scope, currentness and
authorization checks. Read the [contributor notes](CONTRIBUTING.md) before opening a change.

## License

Original code, documentation, THOTH brand assets and THOTH-authored synthetic fixtures in this
snapshot are available under the **[MIT License](LICENSE)**, copyright 2026 THOTH.
Dependencies retain their own licenses; see [third-party and asset notices](THIRD_PARTY_NOTICES.md).

<p align="center">
  <img src="docs/brand/thoth-mark.svg" alt="THOTH connected-evidence symbol" width="40"><br>
  <sub>Evidence. Judgment. Revision.</sub><br>
  <a href="https://github.com/hyperdrivebeep">Built by Hyperdrive</a>
</p>
