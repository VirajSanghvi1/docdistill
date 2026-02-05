# RAGLite

<p align="center">
  <img src="assets/hero.svg" alt="RAGLite: Compress first. Index second." width="900" />
</p>

RAGLite is a local-first CLI that turns a folder of docs (PDF/HTML/TXT/MD) into **structured, low-fluff Markdown** — and then makes it searchable with **Chroma** 🧠 + **ripgrep** 🔎.

Core idea: **compression-before-embeddings** ✂️➡️🧠

<p align="center">
  <img src="assets/diagram.svg" alt="RAGLite workflow: condense, index, query" width="900" />
</p>

## What you get

For each input file:
- `*.execution-notes.md` — practical run/operate notes (checks, failure modes, commands)
- `*.tool-summary.md` — compact index entry (purpose, capabilities, entrypoints, footguns)

Optionally:
- `raglite index` stores embeddings in **Chroma** 🧠 (one DB, many collections)
- `raglite query` runs **hybrid search** 🔎 (vector + keyword)

## Why local + open-source?

If you want a private, local setup (no managed “fancy vector DB” required), RAGLite keeps everything on your machine:
- Distilled Markdown artifacts are plain files you can audit + version control
- Indexing uses **Chroma** (open-source, local) and keyword search uses **ripgrep**
- You can still swap in a hosted vector DB later if you outgrow local

## Engines

RAGLite supports two backends:

- **OpenClaw (recommended):** uses your local OpenClaw Gateway `/v1/responses` endpoint for higher-quality, format-following condensation.
- **Ollama:** uses `POST /api/generate` for fully local inference (often less reliable at strict templates).

## Prereqs

- **Python 3.11+**
- An LLM engine:
  - **OpenClaw** (recommended) 🦞, or
  - **Ollama** 🦙
- For search:
  - **Chroma** (open-source, local) 🧠 at `http://127.0.0.1:8100`

## Install

```bash
# from repo root
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Quickstart (60s)

```bash
# 0) Setup
cd ~/Projects/raglite
source .venv/bin/activate

# 1) Condense → Index (one command)
raglite run /path/to/docs \
  --out ./raglite_out \
  --engine ollama --ollama-model llama3.2:3b \
  --collection my-docs \
  --chroma-url http://127.0.0.1:8100 \
  --skip-indexed

# 2) Query
raglite query ./raglite_out \
  --collection my-docs \
  "rollback procedure"
```

## Usage

### 1) Distill docs ✍️

```bash
raglite condense /path/to/docs \
  --out ./raglite_out \
  --engine openclaw
```

(Or fully local: `--engine ollama --ollama-model llama3.2:3b`.)

### 2) Index distilled output (Chroma)

```bash
raglite index ./raglite_out \
  --collection my-docs \
  --chroma-url http://127.0.0.1:8100
```

### 3) Query (hybrid)

```bash
raglite query ./raglite_out \
  --collection my-docs \
  --top-k 5 \
  --keyword-top-k 5 \
  "rollback procedure"
```

### Useful flags

- `--skip-existing` : don’t redo files that already have both outputs
- `--skip-indexed` : don’t re-embed chunks that are already indexed
- `--nodes` : write per-section nodes + per-doc/root indices
- `--node-max-chars 1200` : keep nodes embed-friendly
- `--sleep-ms 200` : throttle between files (helps avoid timeouts)
- `--max-chars 180000` : cap extracted text per file before summarizing

## Output layout

RAGLite preserves folder structure under your `--out` dir:

```text
<out>/
  some/subdir/file.execution-notes.md
  some/subdir/file.tool-summary.md

(Default output folder is `./raglite_out`.)
```

## Notes / gotchas

- PDF extraction is best-effort: scanned PDFs without embedded text won’t be great.
- If you use `--engine openclaw`, pass `--gateway-token` or set `OPENCLAW_GATEWAY_TOKEN`.
- Indexing defaults to high-signal artifacts (nodes/summaries/notes) and skips `*.outline.md` unless you opt in.

## Roadmap

### Current (implemented)
- `condense` — condense/summarize documents into Markdown artifacts
- `index` — chunk + embed + store in **Chroma** collections
- `query` — retrieve relevant chunks (vector + keyword)
- `run` — one-command pipeline (condense → index)
- Outline + nodes + indices: `--outline`, `--nodes`, root `index.md` + per-doc `*.index.md`

### Next (near-term)
- Detect deletions (prune removed chunks from Chroma)
- Batch upserts to Chroma for speed
- Better query output formatting (snippets + anchors)
- `raglite doctor` (dependency checks)

(Full: [ROADMAP.md](ROADMAP.md))

---

Built to turn “docs” into **usable, searchable tool knowledge**.
