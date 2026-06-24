"""Perp variant of make_bybit_run_core: same open_ws/auth/subscribe/ping/teardown, decoder swapped
to map_bybit_perp so linear executions carry mark_price + position_side='BOTH'."""
from __future__ import annotations

import asyncio

from vike_trader_app.exec.bybit.perp_mapper import map_bybit_perp
from vike_trader_app.exec.bybit.user_data import _bybit_ping, open_bybit_user_data_ws
from vike_trader_app.exec.user_data_core import run_user_data_forever


def make_bybit_perp_run_core(*, ws_url, api_key, api_secret, symbol, now_ms, connect=None):
    """Return a synchronous run_core(emit, stop) that drives the Bybit linear-perp fill stream.

    Identical to make_bybit_run_core except the decode lambda calls map_bybit_perp, which
    enriches each FillEvent with mark_price from the execution row's markPrice field.
    open_ws kwargs are copied VERBATIM from make_bybit_run_core — only decode is swapped.

    ``connect`` is passed through to ``open_bybit_user_data_ws`` for offline/unit testing.
    """

    def run_core(emit, stop):
        asyncio.run(
            run_user_data_forever(
                emit=emit,
                open_ws=lambda: open_bybit_user_data_ws(
                    ws_url=ws_url,
                    api_key=api_key,
                    api_secret=api_secret,
                    now_ms=now_ms,
                    connect=connect,
                    stop=stop,
                    recv_timeout=1.0,
                ),
                decode=lambda frame: map_bybit_perp(frame, venue="bybit", symbol=symbol),
                ping=_bybit_ping,
                stop=stop,
                recv_timeout=1.0,
                now_ms=now_ms,
            )
        )

    return run_core
