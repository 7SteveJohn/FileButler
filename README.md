<p align="right"><b>English</b> · <a href="README.zh-CN.md">简体中文</a></p>

<div align="center">
  <img src="icon/picture.png" width="130" alt="FileButler">
  <h1>FileButler</h1>
  <p><b>Turn a messy drive into a private library you can search — and ask questions to.</b></p>
  <p>
    <a href="https://github.com/7SteveJohn/FileButler/releases/latest"><img src="https://img.shields.io/github/v/release/7SteveJohn/FileButler?color=4f8cff&label=release" alt="release"></a>
    <a href="https://github.com/7SteveJohn/FileButler/actions/workflows/ci.yml"><img src="https://github.com/7SteveJohn/FileButler/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-2ea44f" alt="license"></a>
    <img src="https://img.shields.io/badge/Windows-10%20%2F%2011-0078D6?logo=windows&logoColor=white" alt="platform">
    <img src="https://img.shields.io/badge/data-local%20first-2ea44f" alt="local first">
    <img src="https://img.shields.io/badge/LLM-Ollama%20%2F%20OpenAI%20compatible-4f8cff" alt="llm backends">
  </p>
  <p>
    <a href="https://github.com/7SteveJohn/FileButler/releases/latest"><b>↓ Download</b></a>
    &nbsp;·&nbsp; <a href="#what-it-does">Features</a>
    &nbsp;·&nbsp; <a href="#faq">FAQ</a>
    &nbsp;·&nbsp; <a href="#architecture">Architecture</a>
  </p>
</div>

---

FileButler is a file manager that runs **entirely on your own machine**. It remembers what is on your drive, finds files from a vague description, turns your documents into a knowledge base you can question, and **shows you the plan before it touches a single file**.

AI runs on local open models through [Ollama](https://ollama.com) by default — no data leaves the machine, no API bill. Prefer something else? LM Studio and any OpenAI-compatible cloud endpoint work too; the model and the backend are your choice.

Search, preview, organizing and deduplication **do not depend on AI** — they keep working whether or not a model is running.

| The problem | What FileButler does |
|---|---|
| You need a PDF from three months ago and only remember roughly what it said | Search filename, body text and text inside images at once — or just ask "what was in that quote?" |
| You want an LLM to read your documents, without uploading them to someone's server | Documents are parsed, chunked and embedded locally; a local model answers by default, data stays on disk |
| Your folders are a mess but you are afraid to touch them | Plan first → review and tick each move → execute → undo the whole batch if you don't like it |
| You deleted duplicate files and regretted it | Duplicates are **never deleted** — they are moved to a quarantine folder and can be restored |
| Another AI app means another account and another subscription | Local open models by default, zero cost; switch to LM Studio or a cloud API whenever you want |

## Quick start

1. Grab the latest installer from [Releases](https://github.com/7SteveJohn/FileButler/releases/latest) (~58 MB) and run it — **no administrator rights needed**, it installs into your user profile
2. On first launch it scans Desktop / Documents / Downloads / Pictures, and you can search within seconds
3. Want Q&A and semantic search? Install [Ollama](https://ollama.com); the settings page recommends models for your GPU and downloads them in one click (`qwen3` family plus the `bge-m3` embedding model)
4. Already running LM Studio or a cloud API? Switch the backend in Settings — Ollama is optional

> **Requirements**: Windows 10 / 11, 64-bit. Search, preview, organizing and deduplication are fully local and need no external service.

## What it does

### 🔍 Finding files — more than filename matching

- **Automatic cataloging (Everything-style, read-only)**: scans Desktop / Documents / Downloads / Pictures on startup, watches for changes in the background, new files become searchable within seconds
- **Search syntax**: `ext:pdf`, `size:>100mb`, `dm:week`, `path:Desktop`, `cat:图片`, freely combined with keywords. Long tokens go through an FTS5 full-text index, with pinyin and pinyin-initials matching for Chinese filenames (`baogao`, `bg` → 报告), sortable by relevance. Date keywords accept English (`today` / `week` / `month` / `year`) or Chinese (`本周` / `本月`); category values follow the UI language, so `cat:图片` means "images" in the current Chinese build
- **Real capture dates for photos**: date filters prefer EXIF capture time, so copying or reorganizing files never scrambles a photo timeline; files without EXIF fall back to modified time
- **Natural-language search**: "that quote from last month" resolves through a local vector index (requires Ollama)
- **Semantic image search + OCR**: a vision model (qwen2.5vl) captions images and transcribes the text inside them — search "sunset" for photos, search an invoice number for a screenshot. Without a vision model it degrades to the built-in Windows OCR
- **Built-in preview**: plain text and source code inline, rendered Markdown, PDFs opened in-window, full-size image preview; `Ctrl+F` focuses search, double-click opens the file
- **Filters you don't have to rebuild**: save the current filter set and reopen it later; star, tag and annotate files you return to
- **Batch operations**: multi-select to move / copy / rename / delete (to Recycle Bin) / copy path
- **Two small utilities**: OCR text extraction from images, and SHA-256 checksums for any file
- **English / 简体中文 UI**: switch under Settings → Appearance; the choice is stored in the app's own settings, not the browser locale

### 💬 Asking your documents — a knowledge base that answers

- **Automatic knowledge base**: new documents in watched folders are parsed, chunked and embedded on arrival (can be disabled). Content search and Q&A work without building an index by hand. Supported formats include PDF, Office documents, `.eml` mail, HTML, plain text and source code
- **Q&A plus retrieval**: search raw passages only, or have a local model answer grounded in them. Retrieval fuses vectors with keyword search (reciprocal rank fusion), so keyword hits still work — with citations — even with no model installed. Follow-up questions rewrite the retrieval query automatically, and answers cite their sources (file · section · page)
- **Optional reranking**: a local reranker (bge-reranker-v2-m3, CPU inference) can re-read and reorder the top results for tighter precision. Off by default, and any failure falls back to plain fusion ranking
- **Index any folder**: add directories to the knowledge base manually
- **Conversation history**: sessions are saved; revisit, continue or delete them from the sidebar

### 🗂️ Organizing and cleaning — you see the plan first

- **Smart organizing**: triggered manually; rules plus a local LLM classify files in bulk → a plan is generated → you review and tick each item → moves are executed → **the whole batch can be undone** (and redone). Plans can be saved as templates
- **Duplicate detection with safe cleanup**: three-level hashing finds duplicates → keep-rules (newest / oldest / shortest path) pick the survivor → the rest move to a quarantine folder (not deleted, fully undoable)
- **Two extra health checks**: find large files, detect conflicting copies
- **Never a silent move**: every operation is logged (time / source path / destination path) and undoable per batch

### 🖥️ Daily ergonomics

- **Dashboard**: library size, this week's activity, disk usage by category and top directories on one screen
- **Weekly report**: new files, total size, category breakdown and largest files, exportable
- **Model management**: detects RAM and VRAM, recommends a suitable model, shows installed models with disk usage, one-click download and delete (with confirmation)
- **Tray resident + global hotkey**: closing the window minimizes to tray; `Alt+Space` summons it by default; optional launch at login
- **Dark mode**: follow the system or set it manually
- **Relocatable data directory**: move the database and caches anywhere through a pointer file with integrity checks
- **Database backups**: automatic daily backup on exit, five kept; manual backup / restore / vacuum from Settings
- **Config import and export**: move your settings to another machine without reconfiguring
- **Update check**: pulls new version notices from GitHub Releases
- **Watched folders and exclusions**: add folders, exclude paths and define custom rules — all in Settings

### 🔒 Privacy

- All data lives in a local SQLite database; nothing is sent anywhere by default
- Cloud APIs are **opt-in**: only if you enter an endpoint is your question text sent to the provider you chose. Keys stay in the local database, are masked in the UI, and can be validated with "Test connection" before saving
- You can split the two: chat through a cloud model while embeddings stay local with `bge-m3` — configured independently

## The app at a glance

| Page | What it's for |
|---|---|
| **Dashboard** | Library size, weekly activity, disk usage ranking |
| **Files** | Search, preview, stars and tags, batch operations — the daily driver |
| **Organizer** | Build plans, detect duplicates, run large-file and conflict checks, undo batches |
| **Knowledge base** | Pick folders, ask questions, search only, review past sessions |
| **Settings** | Model backends and downloads, watched folders and exclusions, data migration, backups, updates |

<!-- Screenshots wanted (3–5: dashboard / search results / plan preview / KB answer / model settings).
     Drop them in docs/screenshots/ and replace this comment with:
     <p align="center"><img src="docs/screenshots/files.png" width="760" alt="Files page"></p>
-->

## FAQ

**Do I have to install Ollama?**
No. Search syntax, preview, duplicate detection, rule-based organizing and the built-in Windows OCR all work without a model. Only Q&A, natural-language search and AI classification need one.

**Will my files be uploaded?**
Not by default — with a local model, data never leaves the machine. Only if you configure a cloud API endpoint is **the text of your question** sent to the provider you specified. The files themselves are never uploaded.

**Can organizing lose my files?**
It never moves anything silently. Every move is planned and confirmed item by item, logged after execution, and undoable per batch. Name collisions get a `(1)` / `(2)` suffix instead of overwriting. Duplicates are quarantined, not deleted.

**Will it run on an 8 GB GPU?**
Yes. Settings recommends by VRAM; the 8 GB tier maps to `qwen3:8b` (~6 GB).

**Mac or Linux?**
Windows 10 / 11 64-bit only, for now.

**How do I report a bug or request a feature?**
Open an [issue](https://github.com/7SteveJohn/FileButler/issues).

## Recommended models

Settings probes system RAM and GPU VRAM, then recommends the best-fit Qwen3 model with a download entry:

| Hardware | Model | Size |
|---|---|---|
| 24 GB+ VRAM (RTX 3090/4090 and up) | qwen3:32b | ~22 GB |
| 12–16 GB VRAM (RTX 4070/4060 Ti and similar) | qwen3:14b | ~10 GB |
| 8 GB VRAM / 16 GB+ RAM (no discrete GPU) | qwen3:8b | ~6 GB |
| 8–16 GB RAM (low-end or CPU only) | qwen3:4b | ~3 GB |

Embeddings use `bge-m3` (required, ~1.2 GB). Semantic image search additionally needs the vision model `qwen2.5vl:7b` (~6 GB, optional).

Pull them manually if you prefer: `ollama pull qwen3:8b` / `ollama pull bge-m3`

## Model backends

**Local Ollama (default, free)**: detected automatically. Chat and embedding models stay resident for 30 minutes by default, so they are not unloaded after five idle minutes and reloaded on every question (loading a large model takes tens of seconds). With enough memory, enable preload-on-start and keep-resident for zero-wait first answers.

**LM Studio (local, free)**: start LM Studio's local server, switch **Settings → Model backend** to "Cloud API (OpenAI-compatible)", click "Detect local service" to fill in the defaults (`http://127.0.0.1:1234/v1`, no key required), then "Fetch models" and pick one. Embeddings run through LM Studio's embedding model, so Ollama is not needed at all.

**Cloud API (OpenAI-compatible, optional)**:

- Quick-fill presets for DeepSeek, Zhipu GLM, Kimi, Qwen (DashScope), SiliconFlow, OpenRouter and OpenAI; any OpenAI-compatible endpoint (vLLM, LM Studio, …) also works
- Cost-saving split: chat through a cloud model for quality, embeddings stay local with `bge-m3` for free — configured independently
- Keys are stored only in the local SQLite database, never passed to a third party besides the provider you configured, and never echoed back in the UI
- "Test connection" validates the key and model name before saving

## Architecture

<details>
<summary>Stack, structure and design notes</summary>

UI in Vue 3 + Naive UI, shell in pywebview (Chromium), backend in Python 3.10+.
Storage is SQLite: an FTS5 full-text index, a document-chunk vector index and an image-caption index.
Retrieval fuses vectors and FTS with reciprocal rank fusion (RRF).
Live watching uses watchdog with debouncing. The preview server binds to `127.0.0.1` only and validates every path against a whitelist.
Packaging is PyInstaller (onedir) plus an Inno Setup installer.

```
main.py               entry point (tray resident; --dev attaches to the Vite dev server)
backend/
  api.py              pywebview JS bridge + composition root
  ollama_client.py    Ollama client (batched embed / model download and delete / image understanding)
  db.py               SQLite + vector search (document chunks and image captions)
  fileindex.py        master file index (auto cataloging + filename search)
  watcher.py          watchdog live watching (debounce + auto KB and image queues)
  thumbserver.py      thumbnail/preview server (127.0.0.1 only, whitelist validated)
  core/               scanning / rules / batch classification / plans / undoable execution / dedupe
  knowledge/          parsing / chunking / indexing / RAG / image semantics
frontend/             Vue 3 + Naive UI (Vite build, embedded as a single file)
tests/                automated test suites
```

**Design constraints worth knowing:**

- **No silent writes.** Filesystem mutations are modelled as plans that must be reviewed, are logged, and are reversible per batch. Duplicates move to quarantine rather than being deleted, and name collisions never overwrite.
- **AI is an enhancement layer.** Every core capability has a non-AI path: FTS5 keyword retrieval instead of vectors, rule-based instead of model-based classification, Windows OCR instead of a vision model.
- **Backend neutrality.** Chat and embeddings are configured independently, so local-only, cloud-only and hybrid setups all use the same retrieval pipeline.

</details>

## Development and packaging

<details>
<summary>Build from source</summary>

### Development

```bash
# Backend (Python 3.10+)
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# Frontend (Node 18+)
cd frontend && npm install

# Terminal 1: Vite dev server
cd frontend && npm run dev

# Terminal 2: desktop window attached to the dev server (hot reload)
.venv\Scripts\python main.py --dev
```

### Packaging

```bash
cd frontend && npm run build && cd ..
.venv\Scripts\python -m PyInstaller --noconfirm --onedir --windowed --name FileButler ^
  --add-data "frontend/dist;frontend/dist" --collect-all webview main.py
```

### Tests

```bash
.venv\Scripts\python tests\test_core.py         # organizing engine (no Ollama needed)
.venv\Scripts\python tests\test_fileindex.py    # auto scan/watch/thumbnails (no Ollama needed)
.venv\Scripts\python tests\test_batch2.py       # syntax search/FTS/cleanup/preview/QA history/weekly report/backup/templates
.venv\Scripts\python tests\test_datadir.py      # data directory switching
.venv\Scripts\python tests\test_hardening.py    # connection recycling/backup restore/watcher fallback/Host validation
.venv\Scripts\python tests\test_upgrade.py      # batch indexing/auto knowledge base/autostart (needs Ollama)
.venv\Scripts\python tests\test_ai_classify.py  # AI classification, end to end
.venv\Scripts\python tests\test_rag.py          # knowledge base, end to end
```

</details>

## License

MIT — see [LICENSE](LICENSE).
