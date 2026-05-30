"""Local-embedding + BM25 hybrid knowledge base over the project source and docs.

Chunks files into overlapping line-windows, indexes them with BM25 (lexical) and a dense
embedding matrix (local ``fastembed`` by default), and answers hybrid top-k queries. The
``Embedder`` is injectable so tests use a deterministic stub with no model download. Requires the
optional extra ``pip install vike_trader_app[ai]`` for the real embedder / BM25.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Chunk:
    """A retrievable passage with provenance."""

    text: str
    source: str
    start_line: int


def chunk_text(text: str, source: str, window: int = 40, overlap: int = 10) -> list[Chunk]:
    """Split ``text`` into overlapping line-windows. Windows that are blank-only are dropped."""
    lines = text.splitlines()
    if not lines:
        return []
    step = max(1, window - overlap)
    chunks: list[Chunk] = []
    for start in range(0, len(lines), step):
        block = lines[start:start + window]
        body = "\n".join(block)
        if body.strip():
            chunks.append(Chunk(text=body, source=source, start_line=start + 1))
        if start + window >= len(lines):
            break
    return chunks


def _tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens for BM25."""
    return re.findall(r"[a-z0-9_]+", text.lower())


class KnowledgeBase:
    """Chunks + BM25 lexical index + dense embedding matrix, with hybrid top-k retrieval."""

    def __init__(self, chunks, embeddings, bm25):
        self.chunks = chunks
        self._embeddings = embeddings
        self._bm25 = bm25

    @classmethod
    def build(cls, chunks, embedder) -> "KnowledgeBase":
        """Tokenize for BM25 and embed all chunk texts."""
        raise NotImplementedError

    def query(self, q: str, k: int = 5, *, embedder, alpha: float = 0.5) -> list[dict]:
        """Hybrid top-k: ``alpha``*dense + ``(1-alpha)``*BM25 (both min-max normalized)."""
        raise NotImplementedError
