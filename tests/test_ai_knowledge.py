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
