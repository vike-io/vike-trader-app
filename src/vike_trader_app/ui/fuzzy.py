"""Tiny subsequence fuzzy matcher for the command palette (Phase 5).

Qt-free so the ranking is unit-tested without a widget. ``fuzzy_score`` rewards contiguous
runs and word-boundary hits (so "nc" ranks "New chart" above "Open scre**nc**…"); ``filter_items``
keeps the matches, best first, stable for equal scores.
"""

from __future__ import annotations

_BOUNDARY = " /:-_·>"


def fuzzy_score(query: str, text: str) -> int | None:
    """Score ``text`` against ``query`` as an ordered subsequence; ``None`` if not a match.

    Higher is better. An empty query matches everything with score 0 (palette shows all)."""
    if not query:
        return 0
    q, t = query.lower(), text.lower()
    ti = 0
    score = 0
    last = -2
    for qc in q:
        idx = t.find(qc, ti)
        if idx == -1:
            return None
        if idx == last + 1:
            score += 3                       # contiguous with the previous match
        if idx == 0 or t[idx - 1] in _BOUNDARY:
            score += 4                       # start of a word
        score += 1
        last = idx
        ti = idx + 1
    # prefer shorter targets (a query that nearly fills the label beats one buried in a long one)
    return score - len(text) // 20


def filter_items(query: str, items):
    """``items``: iterable of ``(label, payload)``. Returns matches as ``[(label, payload)]``,
    best score first, original order preserved among equal scores."""
    scored = []
    for i, (label, payload) in enumerate(items):
        s = fuzzy_score(query, label)
        if s is not None:
            scored.append((s, i, label, payload))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [(label, payload) for _s, _i, label, payload in scored]
