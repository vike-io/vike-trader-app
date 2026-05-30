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


def _minmax(scores):
    """Min-max normalize a list of floats to [0,1]; all-equal -> all 0.0."""
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if hi - lo == 0:
        return [0.0] * len(scores)
    return [(s - lo) / (hi - lo) for s in scores]


class FastEmbedEmbedder:
    """Local embeddings via ``fastembed`` (lazy import; downloads a model on first use)."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        try:
            from fastembed import TextEmbedding
        except ImportError as e:  # pragma: no cover - exercised only without the extra
            raise ImportError("knowledge base requires the extra: pip install vike_trader_app[ai]") from e
        self._model = TextEmbedding(model_name=model_name)

    def embed(self, texts):
        return [list(map(float, v)) for v in self._model.embed(list(texts))]


class KnowledgeBase:
    """Chunks + BM25 lexical index + dense embedding matrix, with hybrid top-k retrieval."""

    def __init__(self, chunks, embeddings, bm25):
        self.chunks = chunks
        self._embeddings = embeddings
        self._bm25 = bm25

    @classmethod
    def build(cls, chunks, embedder) -> "KnowledgeBase":
        """Tokenize for BM25 and embed all chunk texts."""
        if not chunks:
            return cls([], [], None)
        from rank_bm25 import BM25Okapi

        bm25 = BM25Okapi([_tokenize(c.text) for c in chunks])
        embeddings = embedder.embed([c.text for c in chunks])
        return cls(list(chunks), embeddings, bm25)

    def query(self, q: str, k: int = 5, *, embedder, alpha: float = 0.5) -> list[dict]:
        """Hybrid top-k: ``alpha``*dense + ``(1-alpha)``*BM25 (both min-max normalized)."""
        if not self.chunks:
            return []
        bm_scores = list(self._bm25.get_scores(_tokenize(q)))
        qv = embedder.embed([q])[0]
        dense = [_cosine(qv, e) for e in self._embeddings]
        combined = [alpha * d + (1 - alpha) * b
                    for d, b in zip(_minmax(dense), _minmax(bm_scores))]
        order = sorted(range(len(self.chunks)), key=lambda i: combined[i], reverse=True)[:k]
        return [
            {"source": self.chunks[i].source, "start_line": self.chunks[i].start_line,
             "text": self.chunks[i].text, "score": float(combined[i])}
            for i in order
        ]

    def save(self, directory) -> None:
        """Persist chunks (JSON) + embeddings (npy). BM25 is rebuilt from chunks on load."""
        import json

        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "chunks.json").write_text(json.dumps(
            [{"text": c.text, "source": c.source, "start_line": c.start_line} for c in self.chunks]
        ), encoding="utf-8")
        import numpy as np

        np.save(directory / "embeddings.npy", np.array(self._embeddings, dtype=float))

    @classmethod
    def load(cls, directory) -> "KnowledgeBase":
        import json

        import numpy as np

        directory = Path(directory)
        raw = json.loads((directory / "chunks.json").read_text(encoding="utf-8"))
        chunks = [Chunk(text=d["text"], source=d["source"], start_line=d["start_line"]) for d in raw]
        embeddings = np.load(directory / "embeddings.npy").tolist()
        bm25 = None
        if chunks:
            from rank_bm25 import BM25Okapi

            bm25 = BM25Okapi([_tokenize(c.text) for c in chunks])
        return cls(chunks, embeddings, bm25)


def _cosine(a, b) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def iter_source_files(root, patterns=(".py", ".md")) -> list[Path]:
    """All files under ``root`` whose suffix is in ``patterns`` (sorted, deterministic)."""
    root = Path(root)
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in patterns)


def build_from_paths(roots, embedder, patterns=(".py", ".md"),
                     window: int = 40, overlap: int = 10) -> "KnowledgeBase":
    """Chunk every matching file under each root and build a KnowledgeBase."""
    chunks: list[Chunk] = []
    for root in roots:
        for path in iter_source_files(root, patterns):
            chunks.extend(chunk_text(path.read_text(encoding="utf-8", errors="ignore"),
                                     source=str(path), window=window, overlap=overlap))
    return KnowledgeBase.build(chunks, embedder)


def default_knowledge_base(embedder, cache_dir=None) -> "KnowledgeBase":
    """Load a cached index, or build one over the installed package source and cache it."""
    pkg_root = Path(__file__).resolve().parents[1]  # src/vike_trader_app
    cache = Path(cache_dir) if cache_dir else pkg_root / ".kb_index"
    if (cache / "chunks.json").exists():
        return KnowledgeBase.load(cache)
    kb = build_from_paths([pkg_root], embedder, patterns=(".py",))
    kb.save(cache)
    return kb
