from __future__ import annotations

import json
import urllib.error
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from chroma_rest import ChromaLoc, add as chroma_add, get_or_create_collection, query as chroma_query


DEFAULT_CHROMA_URL = "http://127.0.0.1:8100"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_EMBED_MODEL = "nomic-embed-text"


@dataclass
class Chunk:
    id: str
    text: str
    meta: dict


def post_json(url: str, data: dict, timeout: int = 120) -> dict:
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="ignore")
        except Exception:
            detail = ""
        raise RuntimeError(f"HTTP {e.code} calling {url}: {detail[:500]}")


def ollama_embed(*, ollama_url: str, model: str, text: str, max_chars: int = 800) -> list[float]:
    """Embed text with Ollama.

    Embedding models have a context limit; we defensively truncate.
    """
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[TRUNCATED_FOR_EMBEDDING]"
    data = post_json(
        f"{ollama_url}/api/embeddings",
        {"model": model, "prompt": text},
        timeout=600,
    )
    emb = data.get("embedding")
    if not isinstance(emb, list):
        raise RuntimeError("ollama embeddings: missing embedding")
    return emb


def iter_md_files(
    root: Path,
    *,
    include_outlines: bool = False,
    include_kinds: set[str] | None = None,
    exclude_kinds: set[str] | None = None,
) -> Iterable[Path]:
    """Yield markdown files that are good retrieval units.

    Default behavior: index nodes + tool summaries + execution notes + indices + root index.
    Skip outlines by default (large + noisy).

    You can further filter by kind using include_kinds/exclude_kinds.
    Kinds: node, tool-summary, execution-notes, index, root-index, outline, md
    """

    def kind_for(p: Path) -> str:
        if "/nodes/" in p.as_posix():
            return "node"
        n = p.name
        if n.endswith(".tool-summary.md"):
            return "tool-summary"
        if n.endswith(".execution-notes.md"):
            return "execution-notes"
        if n.endswith(".outline.md"):
            return "outline"
        if n.endswith(".index.md"):
            return "index"
        if n == "index.md":
            return "root-index"
        return "md"

    for p in root.rglob("*.md"):
        if not p.is_file() or ".docdistill" in p.parts:
            continue

        k = kind_for(p)
        if k == "outline" and not include_outlines:
            continue

        # Default allowlist
        default_ok = k in {"node", "tool-summary", "execution-notes", "index", "root-index"}
        if not default_ok:
            continue

        if include_kinds is not None and k not in include_kinds:
            continue
        if exclude_kinds is not None and k in exclude_kinds:
            continue

        yield p


def kind_from_name(name: str) -> str:
    if name.endswith(".tool-summary.md"):
        return "tool-summary"
    if name.endswith(".execution-notes.md"):
        return "execution-notes"
    if name.endswith(".outline.md"):
        return "outline"
    if name.endswith(".index.md"):
        return "index"
    if name == "index.md":
        return "root-index"
    return "md"


def file_to_chunks(*, file_path: Path, collection: str) -> list[Chunk]:
    text = file_path.read_text(encoding="utf-8", errors="ignore")
    max_chars = 3500

    # Split by H1, otherwise hard split.
    chunks: list[str] = []
    cur: list[str] = []
    for line in text.splitlines():
        if line.startswith("# ") and cur:
            chunks.append("\n".join(cur).strip())
            cur = [line]
        else:
            cur.append(line)
    if cur:
        chunks.append("\n".join(cur).strip())

    final: list[str] = []
    for c in chunks:
        if len(c) <= max_chars:
            final.append(c)
        else:
            for i in range(0, len(c), max_chars):
                final.append(c[i : i + max_chars])

    out: list[Chunk] = []
    for i, c in enumerate(final):
        cid = f"{collection}:{file_path.as_posix()}::{i}"
        out.append(
            Chunk(
                id=cid,
                text=c,
                meta={
                    "path": str(file_path),
                    "name": file_path.name,
                    "kind": kind_from_name(file_path.name),
                },
            )
        )
    return out


def keyword_search(*, root: Path, query: str, top_k: int = 10) -> list[dict]:
    try:
        proc = subprocess.run(
            ["rg", "-n", "--no-heading", "--smart-case", "--max-count", str(top_k), query, str(root)],
            capture_output=True,
            text=True,
            check=False,
        )
        out = proc.stdout.strip().splitlines() if proc.stdout else []
        hits: list[dict] = []
        for line in out:
            parts = line.split(":", 2)
            if len(parts) == 3:
                hits.append({"path": parts[0], "line": int(parts[1]), "text": parts[2]})
        return hits
    except FileNotFoundError:
        return []


def index_distilled_dir(
    *,
    distilled_root: Path,
    chroma_url: str,
    collection: str,
    ollama_url: str,
    embed_model: str,
    embed_max_chars: int = 800,
    sleep_ms: int = 0,
    include_outlines: bool = False,
    include_kinds: set[str] | None = None,
    exclude_kinds: set[str] | None = None,
) -> dict:
    loc = ChromaLoc(base_url=chroma_url)
    c = get_or_create_collection(loc, collection, space="cosine")
    cid = str(c["id"])

    files = list(
        iter_md_files(
            distilled_root,
            include_outlines=include_outlines,
            include_kinds=include_kinds,
            exclude_kinds=exclude_kinds,
        )
    )

    added = 0
    for p in files:
        chunks = file_to_chunks(file_path=p, collection=collection)
        for ch in chunks:
            emb = ollama_embed(ollama_url=ollama_url, model=embed_model, text=ch.text, max_chars=embed_max_chars)
            chroma_add(loc, cid, ids=[ch.id], documents=[ch.text], embeddings=[emb], metadatas=[ch.meta])
            added += 1
            if sleep_ms:
                time.sleep(sleep_ms / 1000.0)

    return {"files": len(files), "chunksAdded": added, "collection": collection}


def query_distilled(
    *,
    query: str,
    distilled_root: Path,
    chroma_url: str,
    collection: str,
    ollama_url: str,
    embed_model: str,
    embed_max_chars: int = 800,
    top_k: int = 10,
    keyword_top_k: int = 10,
) -> dict:
    loc = ChromaLoc(base_url=chroma_url)
    c = get_or_create_collection(loc, collection, space="cosine")
    cid = str(c["id"])

    qemb = ollama_embed(ollama_url=ollama_url, model=embed_model, text=query, max_chars=embed_max_chars)
    vec = chroma_query(loc, cid, query_embeddings=[qemb], n_results=top_k, include=["documents", "metadatas", "distances"])  # type: ignore
    kw = keyword_search(root=distilled_root, query=query, top_k=keyword_top_k)
    return {"vector": vec, "keyword": kw}
