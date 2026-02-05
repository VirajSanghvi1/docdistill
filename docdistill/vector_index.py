from __future__ import annotations

import json
import urllib.error
import subprocess
import time
import urllib.request
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    # Package context
    from .chroma_rest import (
        ChromaLoc,
        get_or_create_collection,
        query as chroma_query,
        upsert as chroma_upsert,
    )
except ImportError:  # pragma: no cover
    # Script context (python docdistill/vector_index.py)
    from chroma_rest import (
        ChromaLoc,
        get_or_create_collection,
        query as chroma_query,
        upsert as chroma_upsert,
    )


DEFAULT_CHROMA_URL = "http://127.0.0.1:8100"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_EMBED_MODEL = "nomic-embed-text"


@dataclass
class Chunk:
    id: str
    text: str
    meta: dict


def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()


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


def _embed_input(text: str, *, max_chars: int) -> str:
    if len(text) > max_chars:
        return text[:max_chars] + "\n[TRUNCATED_FOR_EMBEDDING]"
    return text


def ollama_embed(*, ollama_url: str, model: str, text: str, max_chars: int = 800) -> list[float]:
    """Embed text with Ollama.

    Embedding models have a context limit; we defensively truncate.
    """
    prompt = _embed_input(text, max_chars=max_chars)
    data = post_json(
        f"{ollama_url}/api/embeddings",
        {"model": model, "prompt": prompt},
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


def file_to_chunks(*, distilled_root: Path, file_path: Path, collection: str) -> list[Chunk]:
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

    rel_path = file_path.relative_to(distilled_root).as_posix()

    out: list[Chunk] = []
    for i, c in enumerate(final):
        cid = f"{collection}:{rel_path}::{i}"
        out.append(
            Chunk(
                id=cid,
                text=c,
                meta={
                    "path": str(file_path),
                    "rel_path": rel_path,
                    "name": file_path.name,
                    "kind": kind_from_name(file_path.name),
                },
            )
        )
    return out


def keyword_search(*, root: Path, query: str, top_k: int = 10) -> list[dict]:
    try:
        proc = subprocess.run(
            ["rg", "-n", "--no-heading", "--smart-case", query, str(root)],
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
            if len(hits) >= top_k:
                break
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
    index_cache_path: Path | None = None,
    skip_indexed: bool = False,
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

    cache: dict = {}
    if index_cache_path is not None and index_cache_path.exists():
        try:
            cache = json.loads(index_cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    # Invalidate cache if it was generated for a different target.
    cache_meta = cache.get("__meta__") if isinstance(cache, dict) else None
    if isinstance(cache_meta, dict):
        if cache_meta.get("collection") != collection or cache_meta.get("chroma_url") != chroma_url:
            cache = {}

    added = 0
    skipped = 0
    for p in files:
        chunks = file_to_chunks(distilled_root=distilled_root, file_path=p, collection=collection)
        for ch in chunks:
            key = ch.id
            prompt = _embed_input(ch.text, max_chars=embed_max_chars)
            h = _sha256_text(prompt)
            cached = cache.get(key) if isinstance(cache, dict) else None
            if (
                skip_indexed
                and isinstance(cached, dict)
                and cached.get("sha256") == h
                and cached.get("embed_model") == embed_model
                and int(cached.get("embed_max_chars") or embed_max_chars) == embed_max_chars
            ):
                skipped += 1
                continue

            emb = ollama_embed(ollama_url=ollama_url, model=embed_model, text=ch.text, max_chars=embed_max_chars)
            chroma_upsert(loc, cid, ids=[ch.id], documents=[ch.text], embeddings=[emb], metadatas=[ch.meta])
            added += 1

            if index_cache_path is not None:
                cache[key] = {
                    "sha256": h,
                    "embed_model": embed_model,
                    "embed_max_chars": embed_max_chars,
                    "updatedAt": int(time.time()),
                }

            if sleep_ms:
                time.sleep(sleep_ms / 1000.0)

    if index_cache_path is not None:
        cache["__meta__"] = {
            "collection": collection,
            "chroma_url": chroma_url,
            "updatedAt": int(time.time()),
        }
        index_cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = index_cache_path.with_suffix(index_cache_path.suffix + ".tmp")
        tmp.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(index_cache_path)

    return {"files": len(files), "chunksAdded": added, "chunksSkipped": skipped, "collection": collection}


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
