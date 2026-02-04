# DocPipe 🪐

DocPipe is a CLI that takes a folder full of docs (PDF/HTML/TXT/MD) and spits out **agent-friendly, low-fluff Markdown**.

Think: *less textbook prose, more “what do I run / what do I call / what breaks / what matters.”*

## What it generates

For each input file, DocPipe generates two outputs:

- `*.execution-notes.md` — operational notes for an executor agent (golden path, checks, failure modes)
- `*.tool-summary.md` — ultra-condensed index entry (purpose, capabilities, entrypoints, footguns)

## Engines

DocPipe supports two backends:

- **OpenClaw (recommended):** uses your local OpenClaw Gateway `/v1/responses` endpoint for higher-quality, format-following condensation.
- **Ollama:** uses `POST /api/generate` for fully local inference (often less reliable at strict templates).

## Install

```bash
cd ~/Projects/docpipe
python3 -m venv .venv
source .venv/bin/activate
pip install -r docpipe/requirements.txt
```

## Usage

### Condense a folder (OpenClaw engine)

```bash
cd ~/Projects/docpipe
source .venv/bin/activate

python docpipe/docpipe_cli.py /path/to/docs \
  --out ./docpipe_out \
  --engine openclaw \
  --summary-max-tokens 220 \
  --exec-max-tokens 700
```

### Condense a folder (Ollama engine)

```bash
python docpipe/docpipe_cli.py /path/to/docs \
  --out ./docpipe_out \
  --engine ollama \
  --ollama-model llama3.2:3b
```

### Useful flags

- `--skip-existing` : don’t redo files that already have both outputs
- `--sleep-ms 200` : throttle between files (helps avoid timeouts)
- `--max-chars 180000` : cap extracted text per file before summarizing

## Output layout

DocPipe preserves folder structure under your `--out` dir:

```text
<out>/
  some/subdir/file.execution-notes.md
  some/subdir/file.tool-summary.md
```

## Notes / gotchas

- PDF extraction is best-effort: scanned PDFs without embedded text won’t be great.
- If you use `--engine openclaw`, DocPipe reads the Gateway token from `~/.openclaw/openclaw.json` if you don’t pass `--gateway-token`.
- Token caps strongly affect information density — the next step (planned) is a **hierarchical outline → shard → index** pipeline to avoid loss.

## Roadmap (the fun part)

- **Stage-1 outline** (loss-minimized, high budget)
- **Atomic topic nodes** (200–600 token shards)
- **`index.md` graph** (tiny navigational maps)
- One-command pipeline: `docpipe condense <input> --levels N`

---

Built to turn “docs” into **usable, searchable tool knowledge**.
