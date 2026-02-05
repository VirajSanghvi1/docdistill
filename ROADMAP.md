# DocDistill Roadmap

This roadmap is intentionally specific and low-hype. It reflects what’s implemented today and what’s next.

## Current (implemented)

### Core CLI
- `condense` — condense/summarize documents into Markdown artifacts
- `index` — chunk + embed + store in **Chroma** collections
- `query` — retrieve relevant chunks (vector + keyword)
- `run` — one-command pipeline (condense → index)

### Local-first storage and retrieval
- Named datasets via `--collection`
- **Hybrid search**: vector (Chroma) + keyword (ripgrep)
- Incremental-ish indexing with `--skip-indexed` + `.docdistill/index_cache.json`

### Engines
- **OpenClaw engine** support
- **Ollama engine** support

### Condensation graph
- Stage-1 outline (`--outline`)
- Atomic nodes (`--nodes` with `--node-min-tokens/--node-max-tokens/--node-max-chars`)
- Root + per-doc index maps (`index.md`, `*.index.md`)

---

## Next (near-term)

### Reliability & reproducibility
- Detect deletions (prune removed chunks from Chroma)
- Batch upserts to Chroma for speed
- Clearer dependency checks (`rg`, Chroma reachable, Ollama/OpenClaw reachable)

### Retrieval quality
- Better query output formatting (snippets with surrounding context + source anchors)
- Metadata filters (kind: node vs tool-summary; path prefixes)
- Optional reranking (local model)

### UX
- `docdistill doctor` to validate environment
- `docdistill config` (defaults for engine, chroma url, collection)

---

## Later

### Data management
- Collection stats (counts, embedding model, last indexed)
- Export/import
- Cleanup tools (dedupe, rebuild)

### Format support
- Better PDF extraction controls
- Pluggable loaders

---

## Non-goals (for now)
- Managed/hosted vector DB dependency
- Cloud-only workflows
