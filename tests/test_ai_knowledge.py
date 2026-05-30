"""RAG knowledge base: chunking + hybrid (BM25 + dense) retrieval. Stub embedder = no downloads."""

import pytest

from vike_trader_app.ai.knowledge import Chunk, chunk_text, KnowledgeBase


def test_chunk_text_windows_with_metadata():
    text = "\n".join(f"line {i}" for i in range(100))
    chunks = chunk_text(text, source="foo.py", window=40, overlap=10)
    assert len(chunks) >= 3
    assert all(isinstance(c, Chunk) for c in chunks)
    assert chunks[0].source == "foo.py"
    assert chunks[0].start_line == 1
    assert chunks[1].start_line == 31
    assert "line 0" in chunks[0].text


def test_chunk_text_skips_blank_only_windows():
    chunks = chunk_text("\n\n\n\n", source="x.py", window=2, overlap=0)
    assert chunks == []


class _StubEmbedder:
    """Deterministic bag-of-words embedder over a fixed vocab — no model, fully reproducible."""

    VOCAB = ["alpha", "beta", "gamma", "delta", "engine", "sharpe"]

    def embed(self, texts):
        import re as _re
        out = []
        for t in texts:
            toks = _re.findall(r"[a-z0-9_]+", t.lower())
            out.append([float(toks.count(w)) for w in self.VOCAB])
        return out


def _kb():
    chunks = [
        Chunk(text="alpha alpha engine", source="a.py", start_line=1),
        Chunk(text="beta gamma sharpe", source="b.py", start_line=1),
        Chunk(text="delta delta delta", source="c.py", start_line=1),
    ]
    return KnowledgeBase.build(chunks, _StubEmbedder()), _StubEmbedder()


def test_query_ranks_lexical_and_dense_match_first():
    kb, emb = _kb()
    hits = kb.query("alpha engine", k=2, embedder=emb)
    assert hits[0]["source"] == "a.py"
    assert len(hits) == 2
    assert "score" in hits[0] and "text" in hits[0] and "start_line" in hits[0]


def test_query_returns_empty_for_empty_kb():
    kb = KnowledgeBase.build([], _StubEmbedder())
    assert kb.query("anything", k=5, embedder=_StubEmbedder()) == []


def test_build_from_paths_and_save_load(tmp_path):
    from vike_trader_app.ai.knowledge import build_from_paths

    (tmp_path / "m.py").write_text("\n".join(f"def f{i}(): return {i}" for i in range(60)))
    (tmp_path / "notes.md").write_text("# Title\nalpha engine sharpe notes\n" * 5)
    kb = build_from_paths([tmp_path], _StubEmbedder(), patterns=(".py", ".md"))
    assert len(kb.chunks) >= 2

    out = tmp_path / "index"
    kb.save(out)
    loaded = KnowledgeBase.load(out)
    assert len(loaded.chunks) == len(kb.chunks)
    hits = loaded.query("alpha engine sharpe", k=1, embedder=_StubEmbedder())
    assert hits and hits[0]["source"].endswith("notes.md")


def test_query_kb_service_with_injected_kb():
    from vike_trader_app.ai.services import query_kb

    kb, emb = _kb()
    out = query_kb("alpha engine", k=2, kb=kb, embedder=emb)
    assert out["n"] == 2
    assert out["hits"][0]["source"] == "a.py"
    assert {"source", "start_line", "text", "score"} <= set(out["hits"][0])
