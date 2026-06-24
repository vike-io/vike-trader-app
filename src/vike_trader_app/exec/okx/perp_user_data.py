"""OKX SWAP (linear perp) private-WS factory: same open_ws/auth/teardown as make_okx_run_core,
decoder swapped to map_okx_perp so linear executions carry mark_price + position_side + ct_val
rescaling (contracts -> base). inst_type="SWAP" is threaded through open_okx_user_data_ws.
"""
from __future__ import annotations

import asyncio

from vike_trader_app.exec.okx.perp_mapper import map_okx_perp
from vike_trader_app.exec.okx.user_data import _okx_ping, open_okx_user_data_ws
from vike_trader_app.exec.user_data_core import run_user_data_forever


def make_okx_perp_run_core(
    *,
    ws_url: str,
    api_key: str,
    api_secret: str,
    passphrase: str,
    symbol: str,
    ct_val: float,
    now_ms,
    connect=None,
):
    """Return a synchronous run_core(emit, stop) that drives the OKX SWAP private-WS fill stream.

    Identical to make_okx_run_core except:
    - inst_type="SWAP" is threaded into open_okx_user_data_ws so the subscribe frame carries
      instType="SWAP" (not "SPOT").
    - decode calls map_okx_perp(frame, venue="okx", symbol=symbol, ct_val=ct_val) which rescales
      FillEvent.last_qty from contracts to base (× ct_val), carries mark_price from fillMarkPx/markPx,
      and sets position_side from posSide.

    open_ws kwargs are copied VERBATIM from make_okx_run_core; only inst_type and decode differ.
    ``connect`` is passed through to ``open_okx_user_data_ws`` for offline/unit testing.
    ping_ms=15_000 is well under OKX's 30s idle timeout.
    """

    def run_core(emit, stop):
        asyncio.run(
            run_user_data_forever(
                emit=emit,
                open_ws=lambda: open_okx_user_data_ws(
                    ws_url=ws_url,
                    api_key=api_key,
                    api_secret=api_secret,
                    passphrase=passphrase,
                    now_ms=now_ms,
                    connect=connect,
                    stop=stop,
                    recv_timeout=1.0,
                    inst_type="SWAP",
                ),
                decode=lambda frame: map_okx_perp(frame, venue="okx", symbol=symbol, ct_val=ct_val),
                ping=_okx_ping,
                ping_ms=15_000,
                stop=stop,
                recv_timeout=1.0,
                now_ms=now_ms,
            )
        )

    return run_core
