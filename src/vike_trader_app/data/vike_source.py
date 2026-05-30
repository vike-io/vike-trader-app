"""vike.io OHLCV data source — historical candles via the vike.io MCP `ohlcv` tool.

Pulls candles from ``https://vike.io/mcp`` (the `ohlcv` tool) and maps them to ``Bar``,
following ``next_cursor`` paging so a wide window assembles across pages. The pure parts
(`candles_to_bars`, `collect_pages`) are unit-tested with an injected caller; only
`_mcp_call` performs network I/O. **API only — no direct database access.**

`fetch_bars_range` matches the binance_source fetcher signature, so it drops straight
into ``cache.get_bars(..., fetcher=fetch_bars_range)``.
"""

import json
import os
import urllib.request

from ..core.model import Bar

VIKE_MCP = "https://vike.io/mcp"

# Timeframes the `ohlcv` tool accepts (mirrors its inputSchema enum).
VIKE_INTERVALS = {
    "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "1w",
}

MAX_LIMIT = 5000  # the tool's hard per-call cap


def candles_to_bars(candles: list[dict]) -> list[Bar]:
    """Map ``ohlcv`` candle dicts to Bars. Candle = {ts(ms), open, high, low, close, volume}."""
    return [
        Bar(
            ts=int(c["ts"]),
            open=float(c["open"]),
            high=float(c["high"]),
            low=float(c["low"]),
            close=float(c["close"]),
            volume=float(c.get("volume", 0.0)),
        )
        for c in candles
    ]


def collect_pages(first_args: dict, caller, max_pages: int = 100_000) -> list[dict]:
    """Accumulate candle dicts across pages, following ``next_cursor`` until it is null.

    ``caller(arguments)`` performs one ``ohlcv`` call and returns the decoded payload
    ``{"candles": [...], "next_cursor": str | None}``. The follow-up page carries the
    cursor plus the required ``symbol`` (and ``interval`` when present).
    """
    out: list[dict] = []
    args = dict(first_args)
    for _ in range(max_pages):
        resp = caller(args)
        out.extend(resp.get("candles", []))
        cursor = resp.get("next_cursor")
        if not cursor:
            break
        args = {"symbol": first_args["symbol"], "cursor": cursor}
        if first_args.get("interval") is not None:
            args["interval"] = first_args["interval"]
    return out


def _mcp_call(arguments: dict, token: str | None = None, url: str = VIKE_MCP, timeout: int = 60) -> dict:
    """Call the ``ohlcv`` MCP tool and return its decoded JSON payload (the candle object)."""
    token = token or os.environ.get("vikeio_full_token")
    if not token:
        raise RuntimeError("vikeio_full_token not set (env or .env)")
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "ohlcv", "arguments": arguments}}
    ).encode()
    req = urllib.request.Request(url, data=body, method="POST")  # noqa: S310 - fixed https host
    req.add_header("X-API-KEY", token)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    # vike.io sits behind Cloudflare, which 403s the default Python-urllib UA.
    req.add_header("User-Agent", "vike-trader-app/0.0 (+https://vike.io)")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        envelope = json.loads(resp.read())
    text = envelope["result"]["content"][0]["text"]
    return json.loads(text)


def fetch_bars_range(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    caller=None,
    progress=None,
) -> list[Bar]:
    """Fetch ALL bars in ``[start_ms, end_ms)`` via the vike.io ``ohlcv`` tool (paged).

    Signature matches binance_source so it plugs into ``cache.get_bars``. ``caller`` is the
    injected MCP call (defaults to the live HTTP call); ``progress`` is accepted for parity.
    """
    if interval not in VIKE_INTERVALS:
        raise ValueError(f"unsupported interval {interval!r}; expected one of {sorted(VIKE_INTERVALS)}")
    call = caller if caller is not None else _mcp_call
    first = {
        "symbol": symbol,
        "interval": interval,
        "start": start_ms,
        "end": end_ms,
        "limit": MAX_LIMIT,
    }
    candles = collect_pages(first, call)
    if progress:
        progress(end_ms, start_ms, end_ms)
    return candles_to_bars(candles)
