from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from bs4 import BeautifulSoup
from pypdf import PdfReader


FileKind = Literal["pdf", "html", "htm", "txt"]


@dataclass
class ExtractResult:
    kind: FileKind
    text: str


def _clean_text(s: str) -> str:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def extract_pdf(path: Path) -> ExtractResult:
    reader = PdfReader(str(path))
    parts = []
    for i, page in enumerate(reader.pages):
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        if txt.strip():
            parts.append(f"\n\n--- Page {i+1} ---\n\n{txt}")
    return ExtractResult(kind="pdf", text=_clean_text("\n".join(parts)))


def extract_html(path: Path) -> ExtractResult:
    html = path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "lxml")

    # Remove scripts/styles
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text("\n")
    return ExtractResult(kind="html", text=_clean_text(text))


def extract_txt(path: Path) -> ExtractResult:
    return ExtractResult(kind="txt", text=_clean_text(path.read_text(encoding="utf-8", errors="ignore")))


def extract_file(path: Path) -> ExtractResult:
    suffix = path.suffix.lower().lstrip(".")
    if suffix == "pdf":
        return extract_pdf(path)
    if suffix in ("html", "htm"):
        return extract_html(path)
    return extract_txt(path)
