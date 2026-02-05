# DocDistill

<p align="center">
  <img src="assets/hero.svg" alt="DocDistill: Compress first. Index second." width="900" />
</p>

DocDistill is a local-first CLI that turns a folder of docs (PDF/HTML/TXT/MD) into **low-fluff, LLM-ready Markdown** — and can then **index + query** the distilled output.

Core idea: **compression-before-embeddings**.

## What you get

For each input file:
- `*.execution-notes.md` — practical run/operate notes (checks, failure modes, commands)
- `*.tool-summary.md` — compact index entry (purpose, capabilities, entrypoints, footguns)

Optionally:
- `docdistill index` stores embeddings in **Chroma** (one DB, many collections)
- `docdistill query` runs **hybrid search** (vector + keyword)

## Engines

DocDistill supports two backends:

- **OpenClaw (recommended):** uses your local OpenClaw Gateway `/v1/responses` endpoint for higher-quality, format-following condensation.
- **Ollama:** uses `POST /api/generate` for fully local inference (often less reliable at strict templates).

## Install

```bash
cd ~/Projects/docdistill
python3 -m venv .venv
source .venv/bin/activate
pip install -r docdistill/requirements.txt
```

## Usage

### 1) Distill docs

```bash
cd ~/Projects/docdistill
source .venv/bin/activate

python docdistill/docdistill_cli.py condense /path/to/docs \
  --out ./docdistill_out \
  --engine openclaw
```

(Or fully local: `--engine ollama --ollama-model llama3.2:3b`.)

### 2) Index distilled output (Chroma)

```bash
python docdistill/docdistill_cli.py index ./docdistill_out \
  --collection my-docs \
  --chroma-url http://127.0.0.1:8100
```

### 3) Query (hybrid)

```bash
python docdistill/docdistill_cli.py query ./docdistill_out \
  --collection my-docs \
  --top-k 5 \
  --keyword-top-k 5 \
  "rollback procedure"
```

### Useful flags

- `--skip-existing` : don’t redo files that already have both outputs
- `--sleep-ms 200` : throttle between files (helps avoid timeouts)
- `--max-chars 180000` : cap extracted text per file before summarizing

## Output layout

DocDistill preserves folder structure under your `--out` dir:

```text
<out>/
  some/subdir/file.execution-notes.md
  some/subdir/file.tool-summary.md
```

## Notes / gotchas

- PDF extraction is best-effort: scanned PDFs without embedded text won’t be great.
- If you use `--engine openclaw`, pass `--gateway-token` or set `OPENCLAW_GATEWAY_TOKEN`.
- Indexing defaults to high-signal artifacts (nodes/summaries/notes) and skips `*.outline.md` unless you opt in.

## Roadmap (the fun part)

- **Stage-1 outline** (loss-minimized, high budget)
- **Atomic topic nodes** (200–600 token shards)
- **`index.md` graph** (tiny navigational maps)
- One-command pipeline: `docdistill condense <input> --levels N`

---

Built to turn “docs” into **usable, searchable tool knowledge**.
