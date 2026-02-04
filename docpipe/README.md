# DocPipe (CLI)

DocPipe is a local CLI that converts documentation into two markdown artifacts per file:

1) `*.execution-notes.md` — condensed, execution-relevant notes for an agent/tool-runner.
2) `*.tool-summary.md` — ultra-condensed description for library indexing.

Inputs supported:
- PDF (`.pdf`)
- HTML (`.html`, `.htm`)
- Text/Markdown (`.txt`, `.md`)

Outputs are generated via **local Ollama**.

## Install deps

```bash
cd docpipe
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
cd docpipe
source .venv/bin/activate
python docpipe_cli.py <file-or-folder> --out ./docpipe_out --model llama3.2:3b
```

Notes:
- Output is token-capped via `--exec-max-tokens` and `--summary-max-tokens`.
- Use `--skip-existing` to avoid regenerating.
