#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# NOTE: extract deps (bs4/pypdf) are only needed for `condense`.
# We import extract_file lazily inside the condense path so `index/query` can run without them.

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2:3b"
DEFAULT_GATEWAY_URL = "http://127.0.0.1:18789"


@dataclass
class Prompts:
    outline: str
    execution_notes: str
    tool_summary: str


def build_prompts(*, token_cap_hint: str, outline_max_tokens: int, exec_max_tokens: int, summary_max_tokens: int) -> Prompts:
    # Stage A: loss-minimized outline. Keep very explicit, ban prose.
    outline = f"""You are converting documentation into a LOSS-MINIMIZED, low-fluff OUTLINE for an executor AI.

Return ONLY markdown.

FORMAT:
- Use headings to preserve structure.
- Under each heading, only use bullet lists.
- Prefer exact names, symbols, commands, function names, parameters, file paths.

For each section, prioritize these bullets (when present):
- Definitions / key terms
- Interfaces (commands/APIs/classes/functions) + important fields/flags
- Procedures (step lists)
- Constraints/assumptions
- Failure modes / gotchas
- Examples (short)

RULES:
- No marketing prose. No table of contents. No change log.
- Keep as much factual content as possible.
- Target <= {outline_max_tokens} output tokens.

SOURCE (extracted text) below:
---
"""

    # Stage B1: execution notes (from outline if enabled)
    execution_notes = f"""You are an expert at converting documentation into EXECUTION-RELEVANT notes for an AI agent that can run tools (CLI commands, HTTP calls, scripts, functions).

Return ONLY markdown.

OUTPUT FORMAT (Markdown):
- Title
- What this tool/service is
- When to use
- Inputs (required/optional)
- Outputs
- Preconditions / assumptions
- Golden path (numbered steps)
- Verification checks
- Common errors + fixes
- Safety/rollback notes

RULES:
- Be concise and operational; no marketing.
- Prefer concrete commands, flags, endpoints, example payloads.
- Keep within {token_cap_hint}. Target <= {exec_max_tokens} output tokens.

SOURCE (extracted text) below:
---
"""

    # Stage B2: tool index entry (template)
    tool_summary = f"""You are an expert at writing ULTRA-CONDENSED, AI-readable TOOL INDEX entries.

Return ONLY markdown, and ONLY the filled-in TEMPLATE below.

TEMPLATE (replace the angle-bracket placeholders; keep headings verbatim):

# <TOOL_NAME>

**Purpose:** <ONE_SENTENCE>

**Capabilities:**
- <BULLET>
- <BULLET>
- <BULLET>

**Requires:**
- <BULLET_OR_Unknown>

**Entrypoints:**
- <BULLET_OR_Unknown>

**Limits / footguns:**
- <BULLET>

RULES:
- No table of contents, no prose summary, no history, no change log.
- Prefer symbols, backticks, and short bullets over sentences.
- Rewrite commands is OK, but reference the real command/name/path when the doc provides it.
- If the doc describes multiple components, choose the primary "tool" and mention others only as bullets.
- If a dependency/entrypoint is not explicitly in the source, write `Unknown`.
- Target <= {summary_max_tokens} output tokens.

SOURCE (extracted text) below:
---
"""

    return Prompts(outline=outline, execution_notes=execution_notes, tool_summary=tool_summary)


def iter_input_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return

    exts = {".pdf", ".txt", ".md", ".html", ".htm"}
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            yield p


def post_json(url: str, data: dict, timeout: int = 120, headers: dict[str, str] | None = None) -> dict:
    body = json.dumps(data).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=body, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()


def ollama_generate(*, ollama_url: str, model: str, prompt: str, num_predict: int) -> str:
    data = post_json(
        f"{ollama_url}/api/generate",
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": num_predict,
                "temperature": 0.2,
            },
        },
        timeout=600,
    )
    return (data.get("response") or "").strip()


def openclaw_generate(*, gateway_url: str, gateway_token: str, agent_id: str, prompt: str, max_output_tokens: int) -> str:
    data = post_json(
        f"{gateway_url}/v1/responses",
        {
            "model": "openclaw",
            "input": prompt,
            "max_output_tokens": max_output_tokens,
        },
        timeout=600,
        headers={
            "Authorization": f"Bearer {gateway_token}",
            "x-openclaw-agent-id": agent_id,
        },
    )

    out_parts: list[str] = []
    for item in data.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") == "output_text" and part.get("text"):
                out_parts.append(part["text"])

    return "\n".join(out_parts).strip()


def generate_with_retries(*, engine: str, retries: int, sleep_s: float, prompt: str, max_tokens: int, ollama_url: str, ollama_model: str, gateway_url: str, gateway_token: str, agent_id: str) -> str:
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            if engine == "ollama":
                return ollama_generate(ollama_url=ollama_url, model=ollama_model, prompt=prompt, num_predict=max_tokens)
            return openclaw_generate(gateway_url=gateway_url, gateway_token=gateway_token, agent_id=agent_id, prompt=prompt, max_output_tokens=max_tokens)
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(sleep_s * (attempt + 1))
                continue
            raise
    raise RuntimeError(str(last_err))


def rel_output_path(input_path: Path, input_root: Path, out_root: Path, suffix: str) -> Path:
    if input_root.is_file():
        rel = input_path.name
    else:
        rel = str(input_path.relative_to(input_root))

    base = Path(rel)
    out_rel = base.with_suffix("")
    return out_root / out_rel.parent / (out_rel.name + suffix)


def matches_any(path_str: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path_str, pat) for pat in patterns)


def validate_tool_summary(md: str) -> list[str]:
    # Minimal structural validation (keeps us from silently accepting garbage).
    problems: list[str] = []
    required = [
        "# ",
        "**Purpose:**",
        "**Capabilities:**",
        "**Requires:**",
        "**Entrypoints:**",
        "**Limits / footguns:**",
    ]
    for r in required:
        if r not in md:
            problems.append(f"missing:{r}")

    # place-holder leakage
    if "<TOOL_NAME>" in md or "<ONE_SENTENCE>" in md or "<BULLET" in md:
        problems.append("contains_placeholders")

    # too short tends to mean it failed / got truncated into junk
    if len(md.strip()) < 120:
        problems.append("too_short")

    return problems


def validate_outline(md: str) -> list[str]:
    problems: list[str] = []
    if len(md.strip()) < 400:
        problems.append("too_short")
    # Require at least one heading to avoid useless blobs
    if "#" not in md:
        problems.append("missing_headings")
    # Often indicates model refused / answered generically
    if md.strip().lower().startswith("i can") or md.strip().lower().startswith("sorry"):
        problems.append("refusal_or_meta")
    return problems


def validate_execution_notes(md: str) -> list[str]:
    problems: list[str] = []
    if not md.lstrip().startswith("#"):
        problems.append("missing_title")
    if "Golden path" not in md and "golden path" not in md:
        problems.append("missing_golden_path")
    if len(md.strip()) < 300:
        problems.append("too_short")
    return problems


def estimate_tokens(text: str) -> int:
    """Very rough token estimate (good enough for sizing nodes)."""
    return max(1, int(len(text) / 4))


def shard_outline_to_nodes(
    *,
    outline_md: str,
    nodes_dir: Path,
    node_min_tokens: int = 200,
    node_max_tokens: int = 600,
    node_max_chars: int = 1200,
) -> list[Path]:
    """Split outline into small, embed-friendly topic nodes.

    Strategy:
    - Split by H2 headings (##)
    - If a section is too large, further split into multiple parts.
    - If the final part is too small (< node_min_tokens), merge it into the previous part.
    """
    nodes_dir.mkdir(parents=True, exist_ok=True)

    if node_min_tokens < 1 or node_max_tokens < 1 or node_max_chars < 200:
        raise ValueError("node_min_tokens/node_max_tokens/node_max_chars must be positive")
    if node_min_tokens > node_max_tokens:
        raise ValueError("node_min_tokens must be <= node_max_tokens")

    lines = outline_md.splitlines()
    sections: list[tuple[str, list[str]]] = []
    cur_title = "overview"
    cur_lines: list[str] = []

    def flush():
        nonlocal cur_title, cur_lines
        if cur_lines:
            sections.append((cur_title, cur_lines))
        cur_lines = []

    for line in lines:
        if line.startswith("## "):
            flush()
            cur_title = line[3:].strip() or "section"
            cur_lines.append(line)
        else:
            cur_lines.append(line)
    flush()

    def slugify(title: str, idx: int) -> str:
        slug = (
            title.lower()
            .replace("/", " ")
            .replace("\\", " ")
            .replace(":", " ")
            .replace("  ", " ")
            .strip()
        )
        slug = "-".join([p for p in slug.split() if p])[:60] or f"section-{idx}"
        return slug

    out_paths: list[Path] = []
    out_i = 1

    for sec_i, (title, sec_lines) in enumerate(sections, start=1):
        slug = slugify(title, sec_i)

        sec_text = "\n".join(sec_lines).strip() + "\n"
        if estimate_tokens(sec_text) <= node_max_tokens and len(sec_text) <= node_max_chars:
            p = nodes_dir / f"{out_i:02d}-{slug}.md"
            p.write_text(sec_text, encoding="utf-8")
            out_paths.append(p)
            out_i += 1
            continue

        heading = sec_lines[0] if sec_lines and sec_lines[0].startswith("## ") else f"## {title}"
        body = sec_lines[1:] if sec_lines and sec_lines[0].startswith("## ") else sec_lines

        parts: list[str] = []
        cur: list[str] = [heading]

        for line in body:
            candidate = "\n".join(cur + [line]).strip() + "\n"
            if (estimate_tokens(candidate) > node_max_tokens or len(candidate) > node_max_chars) and len(cur) > 1:
                parts.append("\n".join(cur).strip() + "\n")
                cur = [heading, line]
            else:
                cur.append(line)

        if len(cur) > 1:
            parts.append("\n".join(cur).strip() + "\n")

        # Merge trailing tiny part into previous part.
        if len(parts) >= 2 and estimate_tokens(parts[-1]) < node_min_tokens:
            parts[-2] = parts[-2].rstrip() + "\n" + parts[-1]
            parts = parts[:-1]

        for part_i, text in enumerate(parts, start=1):
            # Cap to ensure embed-friendly nodes.
            if len(text) > node_max_chars:
                text = text[:node_max_chars] + "\n\n[TRUNCATED]\n"

            suffix = f"--p{part_i:02d}" if len(parts) > 1 else ""
            p = nodes_dir / f"{out_i:02d}-{slug}{suffix}.md"
            p.write_text(text, encoding="utf-8")
            out_paths.append(p)
            out_i += 1

    return out_paths


def write_doc_index(*, doc_index_path: Path, source_path: Path, tool_summary: Path, execution_notes: Path, outline_path: Path | None, node_paths: list[Path]) -> None:
    doc_index_path.parent.mkdir(parents=True, exist_ok=True)

    rel = lambda p: p.name if p.parent == doc_index_path.parent else str(p.relative_to(doc_index_path.parent))

    lines: list[str] = []
    lines.append(f"# {source_path.stem}")
    lines.append("")
    lines.append("## Outputs")
    lines.append(f"- Tool summary: [{tool_summary.name}]({rel(tool_summary)})")
    lines.append(f"- Execution notes: [{execution_notes.name}]({rel(execution_notes)})")
    if outline_path:
        lines.append(f"- Outline: [{outline_path.name}]({rel(outline_path)})")
    if node_paths:
        lines.append("")
        lines.append("## Nodes")
        for p in node_paths:
            lines.append(f"- [{p.name}]({rel(p)})")
    lines.append("")
    lines.append("## Source")
    lines.append(f"- `{source_path}`")
    lines.append("")

    doc_index_path.write_text("\n".join(lines), encoding="utf-8")


def write_root_index(*, out_root: Path, input_root: Path, doc_indices: list[Path]) -> None:
    """Human-browsable top index that links to per-doc indices."""
    index_path = out_root / "index.md"
    lines: list[str] = []
    lines.append("# DocDistill Index")
    lines.append("")
    lines.append(f"- Source: `{input_root}`")
    lines.append(f"- Generated: {time.strftime('%Y-%m-%d %H:%M:%S %Z', time.localtime())}")
    lines.append("")

    if not doc_indices:
        lines.append("(No per-doc indices were generated. Run with `--nodes`.)")
        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return

    lines.append("## Documents")
    for p in sorted(doc_indices):
        rel = str(p.relative_to(out_root))
        lines.append(f"- [{p.stem}]({rel})")

    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_cache(cache_path: Path) -> dict:
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return {"files": {}}


def save_cache(cache_path: Path, cache: dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="DocDistill CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # --- Condense ---
    ap_c = sub.add_parser("condense", help="Extract + distill docs into markdown artifacts")
    ap_c.add_argument("input", help="File or directory to process")
    ap_c.add_argument("--out", default="./docdistill_out", help="Output directory")

    ap_c.add_argument("--engine", choices=["ollama", "openclaw"], default="ollama", help="Generation engine")

    ap_c.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    ap_c.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL)

    ap_c.add_argument("--gateway-url", default=DEFAULT_GATEWAY_URL)
    ap_c.add_argument("--gateway-token", default=os.environ.get("OPENCLAW_GATEWAY_TOKEN", ""))
    ap_c.add_argument("--gateway-agent-id", default="main")

    ap_c.add_argument("--exec-max-tokens", type=int, default=1200)
    ap_c.add_argument("--summary-max-tokens", type=int, default=350)
    ap_c.add_argument("--outline", action="store_true", help="Generate a loss-minimized outline first and summarize from it")
    ap_c.add_argument("--outline-max-tokens", type=int, default=5000)
    ap_c.add_argument("--nodes", action="store_true", help="Write outline shards + per-doc index linking nodes")
    ap_c.add_argument("--node-min-tokens", type=int, default=200, help="Minimum target size for a node (approx tokens). Small trailing parts are merged.")
    ap_c.add_argument("--node-max-tokens", type=int, default=600, help="Maximum target size for a node (approx tokens)")
    ap_c.add_argument("--node-max-chars", type=int, default=1200, help="Hard cap for node size (chars) to keep nodes embed-friendly")

    ap_c.add_argument("--max-chars", type=int, default=180_000, help="Max extracted chars per file")
    ap_c.add_argument("--sleep-ms", type=int, default=0, help="Sleep between files (throttle)")

    ap_c.add_argument("--skip-existing", action="store_true")
    ap_c.add_argument("--only", choices=["all", "tool-summary", "execution-notes"], default="all")

    ap_c.add_argument("--include", action="append", default=[], help="Glob(s) to include (default: all)")
    ap_c.add_argument("--exclude", action="append", default=[], help="Glob(s) to exclude")
    ap_c.add_argument("--max-files", type=int, default=0, help="Process at most N files (0 = no limit)")

    ap_c.add_argument("--retries", type=int, default=2)
    ap_c.add_argument("--retry-sleep", type=float, default=1.5)
    ap_c.add_argument("--max-errors", type=int, default=10)
    ap_c.add_argument("--fail-fast", action="store_true")
    ap_c.add_argument("--validate", action="store_true", help="Validate outputs; retry once if invalid")

    # --- Index ---
    ap_i = sub.add_parser("index", help="Embed + store a distilled directory into Chroma")
    ap_i.add_argument("distilled", help="Distilled output directory (from condense)")
    ap_i.add_argument("--collection", required=True, help="Chroma collection name")
    ap_i.add_argument("--chroma-url", default="http://127.0.0.1:8100")
    ap_i.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    ap_i.add_argument("--embed-model", default="nomic-embed-text")
    ap_i.add_argument("--embed-max-chars", type=int, default=800, help="Max chars passed into embedding model")
    ap_i.add_argument("--sleep-ms", type=int, default=0)
    ap_i.add_argument("--include-outlines", action="store_true", help="Also index *.outline.md (default: skip)")
    ap_i.add_argument(
        "--include-kinds",
        default="",
        help="Comma-separated kinds to include (filters default set). Kinds: node,tool-summary,execution-notes,index,root-index,outline,md",
    )
    ap_i.add_argument(
        "--exclude-kinds",
        default="",
        help="Comma-separated kinds to exclude. Kinds: node,tool-summary,execution-notes,index,root-index,outline,md",
    )
    ap_i.add_argument("--skip-indexed", action="store_true", help="Skip chunks already indexed (via .docdistill/index_cache.json)")

    # --- Run (condense + index) ---
    ap_r = sub.add_parser("run", help="One-command pipeline: condense then index into a single Chroma collection")
    ap_r.add_argument("input", help="File or directory to process")
    ap_r.add_argument("--out", default="./docdistill_out", help="Output directory")
    ap_r.add_argument("--collection", required=True, help="Chroma collection name")
    ap_r.add_argument("--chroma-url", default="http://127.0.0.1:8100")
    ap_r.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    ap_r.add_argument("--embed-model", default="nomic-embed-text")
    ap_r.add_argument("--embed-max-chars", type=int, default=800)
    ap_r.add_argument(
        "--skip-indexed",
        action="store_true",
        help="Skip chunks already indexed (via .docdistill/index_cache.json)",
    )
    ap_r.add_argument("--include-outlines", action="store_true")
    ap_r.add_argument("--include-kinds", default="")
    ap_r.add_argument("--exclude-kinds", default="")

    # Condense options (same as condense)
    ap_r.add_argument("--engine", choices=["ollama", "openclaw"], default="ollama")
    ap_r.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL)
    ap_r.add_argument("--gateway-url", default=DEFAULT_GATEWAY_URL)
    ap_r.add_argument("--gateway-token", default=os.environ.get("OPENCLAW_GATEWAY_TOKEN", ""))
    ap_r.add_argument("--gateway-agent-id", default="main")
    ap_r.add_argument("--exec-max-tokens", type=int, default=1200)
    ap_r.add_argument("--summary-max-tokens", type=int, default=350)
    ap_r.add_argument("--outline", action="store_true")
    ap_r.add_argument("--outline-max-tokens", type=int, default=5000)
    ap_r.add_argument("--nodes", action="store_true")
    ap_r.add_argument("--node-min-tokens", type=int, default=200)
    ap_r.add_argument("--node-max-tokens", type=int, default=600)
    ap_r.add_argument("--node-max-chars", type=int, default=1200)
    ap_r.add_argument("--max-chars", type=int, default=180_000)
    ap_r.add_argument("--sleep-ms", type=int, default=0)
    ap_r.add_argument("--skip-existing", action="store_true")
    ap_r.add_argument("--only", choices=["all", "tool-summary", "execution-notes"], default="all")
    ap_r.add_argument("--include", action="append", default=[])
    ap_r.add_argument("--exclude", action="append", default=[])
    ap_r.add_argument("--max-files", type=int, default=0)
    ap_r.add_argument("--retries", type=int, default=2)
    ap_r.add_argument("--retry-sleep", type=float, default=1.5)
    ap_r.add_argument("--max-errors", type=int, default=10)
    ap_r.add_argument("--fail-fast", action="store_true")
    ap_r.add_argument("--validate", action="store_true")

    # --- Query ---
    ap_q = sub.add_parser("query", help="Hybrid search (vector + keyword) over an indexed distilled directory")
    ap_q.add_argument("distilled", help="Distilled output directory")
    ap_q.add_argument("--collection", required=True)
    ap_q.add_argument("--chroma-url", default="http://127.0.0.1:8100")
    ap_q.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    ap_q.add_argument("--embed-model", default="nomic-embed-text")
    ap_q.add_argument("--embed-max-chars", type=int, default=800, help="Max chars passed into embedding model")
    ap_q.add_argument("--top-k", type=int, default=10)
    ap_q.add_argument("--keyword-top-k", type=int, default=10)
    ap_q.add_argument("query", help="Search query")

    args = ap.parse_args()

    # --- Index command ---
    if args.cmd == "index":
        try:
            from .vector_index import index_distilled_dir
        except ImportError:  # pragma: no cover
            from vector_index import index_distilled_dir

        def _parse_csv_set(s: str) -> set[str] | None:
            parts = [p.strip() for p in (s or "").split(",") if p.strip()]
            return set(parts) if parts else None

        distilled_root = Path(args.distilled).expanduser().resolve()
        index_cache_path = (distilled_root / ".docdistill" / "index_cache.json") if args.skip_indexed else None

        res = index_distilled_dir(
            distilled_root=distilled_root,
            chroma_url=args.chroma_url,
            collection=args.collection,
            ollama_url=args.ollama_url,
            embed_model=args.embed_model,
            embed_max_chars=args.embed_max_chars,
            sleep_ms=args.sleep_ms,
            include_outlines=bool(args.include_outlines),
            include_kinds=_parse_csv_set(args.include_kinds),
            exclude_kinds=_parse_csv_set(args.exclude_kinds),
            index_cache_path=index_cache_path,
            skip_indexed=bool(args.skip_indexed),
        )
        print(json.dumps(res, indent=2))
        return 0

    # --- Query command ---
    if args.cmd == "query":
        try:
            from .vector_index import query_distilled
        except ImportError:  # pragma: no cover
            from vector_index import query_distilled

        res = query_distilled(
            query=args.query,
            distilled_root=Path(args.distilled).expanduser().resolve(),
            chroma_url=args.chroma_url,
            collection=args.collection,
            ollama_url=args.ollama_url,
            embed_model=args.embed_model,
            embed_max_chars=args.embed_max_chars,
            top_k=args.top_k,
            keyword_top_k=args.keyword_top_k,
        )
        print(json.dumps(res, indent=2))
        return 0

    # --- Condense / Run command ---
    if args.cmd == "run":
        # 1) condense into --out
        run_condense_args = args
        # reuse the same condense path by setting fields as expected below
        args = run_condense_args
        args.cmd = "condense"  # type: ignore[attr-defined]

        # After condense finishes, we index the output.
        should_index_after = True
    else:
        should_index_after = False

    assert args.cmd == "condense"

    input_root = Path(args.input).expanduser().resolve()
    out_root = Path(args.out).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    meta_dir = out_root / ".docdistill"
    cache_path = meta_dir / "cache.json"
    errors_log = meta_dir / "errors.log"
    run_stats_path = meta_dir / "run_stats.json"

    cache = load_cache(cache_path)

    gateway_token = args.gateway_token
    if args.engine == "openclaw" and not gateway_token:
        try:
            cfg_path = Path("~/.openclaw/openclaw.json").expanduser()
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            gateway_token = str(cfg.get("gateway", {}).get("auth", {}).get("token", ""))
        except Exception:
            gateway_token = ""

    if args.engine == "openclaw" and not gateway_token:
        print("ERROR: --gateway-token (or OPENCLAW_GATEWAY_TOKEN) is required for engine=openclaw", file=sys.stderr)
        return 2

    prompts = build_prompts(
        token_cap_hint=f"~{args.exec_max_tokens} tokens max",
        outline_max_tokens=args.outline_max_tokens,
        exec_max_tokens=args.exec_max_tokens,
        summary_max_tokens=args.summary_max_tokens,
    )

    all_files = list(iter_input_files(input_root))
    if not all_files:
        print("No matching files found.", file=sys.stderr)
        return 2

    # include/exclude filtering is based on path relative to input root
    files: list[Path] = []
    for p in all_files:
        rel = p.name if input_root.is_file() else str(p.relative_to(input_root))
        if args.include and not matches_any(rel, args.include):
            continue
        if args.exclude and matches_any(rel, args.exclude):
            continue
        files.append(p)

    total = len(files)
    if args.max_files and args.max_files > 0:
        files = files[: args.max_files]

    start = time.time()
    stats = {
        "engine": args.engine,
        "ollama_model": args.ollama_model,
        "outline": bool(args.outline),
        "nodes": bool(args.nodes),
        "input": str(input_root),
        "out": str(out_root),
        "total_candidates": total,
        "processed": 0,
        "ok": 0,
        "skipped": 0,
        "errors": 0,
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    def log_error(msg: str) -> None:
        errors_log.parent.mkdir(parents=True, exist_ok=True)
        with errors_log.open("a", encoding="utf-8", errors="ignore") as f:
            f.write(msg + "\n")

    doc_indices: list[Path] = []

    for idx, p in enumerate(files, start=1):
        stats["processed"] += 1
        rel = p.name if input_root.is_file() else str(p.relative_to(input_root))

        try:
            try:
                from .extract import extract_file
            except ImportError:  # pragma: no cover
                from extract import extract_file

            extracted = extract_file(p)
            text = extracted.text
            if len(text) > args.max_chars:
                text = text[: args.max_chars] + "\n\n[TRUNCATED]"

            text_hash = sha256_text(text)

            exec_out = rel_output_path(p, input_root, out_root, ".execution-notes.md")
            sum_out = rel_output_path(p, input_root, out_root, ".tool-summary.md")
            outline_out = rel_output_path(p, input_root, out_root, ".outline.md")
            nodes_dir = rel_output_path(p, input_root, out_root, "")
            nodes_dir = nodes_dir.parent / nodes_dir.name  # directory for this doc
            nodes_dir = nodes_dir / "nodes"
            doc_index = rel_output_path(p, input_root, out_root, "")
            doc_index = doc_index.parent / (doc_index.name + ".index.md")

            exec_out.parent.mkdir(parents=True, exist_ok=True)

            cached = cache.get("files", {}).get(rel)
            if args.skip_existing and exec_out.exists() and sum_out.exists():
                stats["skipped"] += 1
                continue
            if cached and cached.get("hash") == text_hash and exec_out.exists() and sum_out.exists() and args.skip_existing:
                stats["skipped"] += 1
                continue

            print(f"[{idx}/{len(files)}] {rel}", flush=True)

            source_for_stage_b = text

            node_paths: list[Path] = []
            if args.outline or args.nodes:
                outline_md = generate_with_retries(
                    engine=args.engine,
                    retries=args.retries,
                    sleep_s=args.retry_sleep,
                    prompt=prompts.outline + text,
                    max_tokens=args.outline_max_tokens,
                    ollama_url=args.ollama_url,
                    ollama_model=args.ollama_model,
                    gateway_url=args.gateway_url,
                    gateway_token=gateway_token,
                    agent_id=args.gateway_agent_id,
                )

                if args.validate:
                    probs = validate_outline(outline_md)
                    if probs:
                        outline_md = generate_with_retries(
                            engine=args.engine,
                            retries=0,
                            sleep_s=args.retry_sleep,
                            prompt=prompts.outline + text,
                            max_tokens=args.outline_max_tokens,
                            ollama_url=args.ollama_url,
                            ollama_model=args.ollama_model,
                            gateway_url=args.gateway_url,
                            gateway_token=gateway_token,
                            agent_id=args.gateway_agent_id,
                        )

                outline_out.write_text(outline_md + "\n", encoding="utf-8")
                source_for_stage_b = outline_md

                if args.nodes:
                    node_paths = shard_outline_to_nodes(
                        outline_md=outline_md,
                        nodes_dir=nodes_dir,
                        node_min_tokens=args.node_min_tokens,
                        node_max_tokens=args.node_max_tokens,
                        node_max_chars=args.node_max_chars,
                    )

            exec_md = ""
            sum_md = ""

            if args.only in ("all", "execution-notes"):
                exec_md = generate_with_retries(
                    engine=args.engine,
                    retries=args.retries,
                    sleep_s=args.retry_sleep,
                    prompt=prompts.execution_notes + source_for_stage_b,
                    max_tokens=args.exec_max_tokens,
                    ollama_url=args.ollama_url,
                    ollama_model=args.ollama_model,
                    gateway_url=args.gateway_url,
                    gateway_token=gateway_token,
                    agent_id=args.gateway_agent_id,
                )

            if args.only in ("all", "tool-summary"):
                sum_md = generate_with_retries(
                    engine=args.engine,
                    retries=args.retries,
                    sleep_s=args.retry_sleep,
                    prompt=prompts.tool_summary + source_for_stage_b,
                    max_tokens=args.summary_max_tokens,
                    ollama_url=args.ollama_url,
                    ollama_model=args.ollama_model,
                    gateway_url=args.gateway_url,
                    gateway_token=gateway_token,
                    agent_id=args.gateway_agent_id,
                )

            # Validation + one extra retry if requested
            if args.validate:
                if sum_md:
                    probs = validate_tool_summary(sum_md)
                    if probs:
                        sum_md = generate_with_retries(
                            engine=args.engine,
                            retries=0,
                            sleep_s=args.retry_sleep,
                            prompt=prompts.tool_summary + source_for_stage_b,
                            max_tokens=args.summary_max_tokens,
                            ollama_url=args.ollama_url,
                            ollama_model=args.ollama_model,
                            gateway_url=args.gateway_url,
                            gateway_token=gateway_token,
                            agent_id=args.gateway_agent_id,
                        )
                if exec_md:
                    probs = validate_execution_notes(exec_md)
                    if probs:
                        exec_md = generate_with_retries(
                            engine=args.engine,
                            retries=0,
                            sleep_s=args.retry_sleep,
                            prompt=prompts.execution_notes + source_for_stage_b,
                            max_tokens=args.exec_max_tokens,
                            ollama_url=args.ollama_url,
                            ollama_model=args.ollama_model,
                            gateway_url=args.gateway_url,
                            gateway_token=gateway_token,
                            agent_id=args.gateway_agent_id,
                        )

            if exec_md:
                exec_out.write_text(exec_md.strip() + "\n", encoding="utf-8")
            if sum_md:
                sum_out.write_text(sum_md.strip() + "\n", encoding="utf-8")

            if args.nodes:
                write_doc_index(
                    doc_index_path=doc_index,
                    source_path=p,
                    tool_summary=sum_out,
                    execution_notes=exec_out,
                    outline_path=outline_out if outline_out.exists() else None,
                    node_paths=node_paths,
                )
                doc_indices.append(doc_index)

            cache.setdefault("files", {})[rel] = {
                "hash": text_hash,
                "engine": args.engine,
                "ollama_model": args.ollama_model,
                "outline": bool(args.outline),
                "nodes": bool(args.nodes),
                "ts": time.time(),
            }
            save_cache(cache_path, cache)

            stats["ok"] += 1

            if args.sleep_ms:
                time.sleep(args.sleep_ms / 1000.0)

        except Exception as e:
            stats["errors"] += 1
            msg = f"ERROR {rel}: {e!r}"
            print(msg, file=sys.stderr, flush=True)
            try:
                log_error(msg)
            except Exception:
                pass
            if args.fail_fast or stats["errors"] >= args.max_errors:
                break

    # Root index is generated when --nodes is enabled.
    if args.nodes:
        try:
            write_root_index(out_root=out_root, input_root=input_root, doc_indices=doc_indices)
        except Exception as e:
            log_error(f"ERROR write_root_index: {e!r}")

    stats["durationSeconds"] = round(time.time() - start, 2)
    run_stats_path.parent.mkdir(parents=True, exist_ok=True)
    run_stats_path.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")

    # If this was `run`, perform indexing as step 2.
    if should_index_after and stats["errors"] == 0:
        try:
            try:
                from .vector_index import index_distilled_dir
            except ImportError:  # pragma: no cover
                from vector_index import index_distilled_dir

            def _parse_csv_set(s: str) -> set[str] | None:
                parts = [p.strip() for p in (s or "").split(",") if p.strip()]
                return set(parts) if parts else None

            index_cache_path = (out_root / ".docdistill" / "index_cache.json") if bool(run_condense_args.skip_indexed) else None

            index_res = index_distilled_dir(
                distilled_root=out_root,
                chroma_url=run_condense_args.chroma_url,
                collection=run_condense_args.collection,
                ollama_url=run_condense_args.ollama_url,
                embed_model=run_condense_args.embed_model,
                embed_max_chars=run_condense_args.embed_max_chars,
                include_outlines=bool(run_condense_args.include_outlines),
                include_kinds=_parse_csv_set(run_condense_args.include_kinds),
                exclude_kinds=_parse_csv_set(run_condense_args.exclude_kinds),
                index_cache_path=index_cache_path,
                skip_indexed=bool(run_condense_args.skip_indexed),
            )
            # attach indexing stats to run_stats.json for visibility
            stats["index"] = index_res
            run_stats_path.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
        except Exception as e:
            print(f"ERROR: indexing failed: {e!r}", file=sys.stderr)
            return 1
    elif should_index_after and stats["errors"] != 0:
        print("NOTE: skipping indexing because condense reported errors", file=sys.stderr)

    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
