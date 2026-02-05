from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class ChromaLoc:
    base_url: str = "http://127.0.0.1:8100"
    tenant: str = "default_tenant"
    database: str = "default_database"

    def collections_url(self) -> str:
        return f"{self.base_url}/api/v2/tenants/{self.tenant}/databases/{self.database}/collections"


def _req_json(method: str, url: str, body: dict | None = None, timeout: int = 120) -> dict | list:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            if not raw:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="ignore")
        except Exception:
            detail = ""
        raise RuntimeError(f"Chroma HTTP {e.code} calling {url}: {detail[:500]}")


def list_collections(loc: ChromaLoc) -> list[dict]:
    res = _req_json("GET", loc.collections_url())
    if not isinstance(res, list):
        raise RuntimeError(f"Expected list from Chroma list_collections, got {type(res).__name__}")
    return res


def get_or_create_collection(loc: ChromaLoc, name: str, *, space: str = "cosine") -> dict:
    cols = list_collections(loc)
    for c in cols:
        if c.get("name") == name:
            return c

    created = _req_json(
        "POST",
        loc.collections_url(),
        {"name": name, "metadata": {"hnsw:space": space}},
    )
    if not isinstance(created, dict):
        raise RuntimeError(f"Expected dict from Chroma create_collection, got {type(created).__name__}")
    return created


def add(
    loc: ChromaLoc,
    collection_id: str,
    *,
    ids: list[str],
    documents: list[str],
    embeddings: list[list[float]],
    metadatas: list[dict] | None = None,
) -> None:
    url = f"{loc.collections_url()}/{collection_id}/add"
    body: dict = {"ids": ids, "documents": documents, "embeddings": embeddings}
    if metadatas is not None:
        body["metadatas"] = metadatas
    _req_json("POST", url, body, timeout=600)


def upsert(
    loc: ChromaLoc,
    collection_id: str,
    *,
    ids: list[str],
    documents: list[str],
    embeddings: list[list[float]],
    metadatas: list[dict] | None = None,
) -> None:
    """Upsert records into a collection.

    Chroma's /upsert updates existing ids and inserts new ones.
    """
    url = f"{loc.collections_url()}/{collection_id}/upsert"
    body: dict = {"ids": ids, "documents": documents, "embeddings": embeddings}
    if metadatas is not None:
        body["metadatas"] = metadatas
    _req_json("POST", url, body, timeout=600)


def query(
    loc: ChromaLoc,
    collection_id: str,
    *,
    query_embeddings: list[list[float]],
    n_results: int = 10,
    include: list[str] | None = None,
) -> dict:
    url = f"{loc.collections_url()}/{collection_id}/query"
    body: dict = {"query_embeddings": query_embeddings, "n_results": n_results}
    if include is not None:
        body["include"] = include
    res = _req_json("POST", url, body, timeout=600)
    assert isinstance(res, dict)
    return res
