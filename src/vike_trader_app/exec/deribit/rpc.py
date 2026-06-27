"""Pure JSON-RPC 2.0 helpers for the Deribit adapter (no socket).

JsonRpcBuilder hands out monotonically increasing request ids so a request/response transport (6b) can
correlate replies on the shared socket. parse_response splits a frame into (id, result, error) — a
well-formed JSON-RPC response carries exactly one of result/error.
"""
from __future__ import annotations

from itertools import count


class JsonRpcBuilder:
    """Monotonic-id JSON-RPC 2.0 request builder. One instance per logical connection."""

    def __init__(self, *, start: int = 1) -> None:
        self._counter = count(start)

    def next_id(self) -> int:
        return next(self._counter)

    def request(self, method: str, params: dict) -> dict:
        return {"jsonrpc": "2.0", "id": self.next_id(), "method": method, "params": params}


def parse_response(frame) -> tuple[int | None, dict | None, dict | None]:
    """(id, result, error) from a JSON-RPC response frame; (None, None, None) for non-dict/keepalive."""
    if not isinstance(frame, dict):
        return None, None, None
    rid = frame.get("id")
    rid = rid if isinstance(rid, int) else None
    return rid, frame.get("result"), frame.get("error")
