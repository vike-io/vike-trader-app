"""OKX tdMode auto-detection from GET /api/v5/account/config acctLv.

acctLv "1"/"2" (Spot / Spot-and-futures)     → tdMode "cash"
acctLv "3"/"4" (Multi-currency / Portfolio)  → tdMode "cross"
Result is cached on the instance after the first fetch.
"""

from __future__ import annotations

import pytest

from vike_trader_app.exec.okx.client import OKXSpotExecutionClient
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.events import OrderRequest

_FILTERS = {"tick_size": 0.01, "step_size": 0.001, "min_qty": 0.001,
            "max_qty": 9000.0, "min_notional": 5.0}

_PATH_CONFIG = "/api/v5/account/config"
_PATH_ORDER = "/api/v5/trade/order"


def _config_resp(acct_lv: str) -> dict:
    return {"code": "0", "data": [{"acctLv": acct_lv, "uid": "12345"}]}


def _order_ok() -> dict:
    return {"code": "0", "data": [{"ordId": "99", "clOrdId": "r-0", "sCode": "0", "sMsg": ""}]}


def _limit_req(coid="r-0") -> OrderRequest:
    return OrderRequest(client_order_id=coid, venue="okx", symbol="BTC-USDT",
                        side=+1, qty=0.01, order_type="limit", price=60000.0)


def _make_transport(acct_lv: str):
    """Return a transport stub that serves /account/config + /trade/order."""
    def _transport(base, path, method, params, signer, **kw):
        if path == _PATH_CONFIG:
            return _config_resp(acct_lv)
        if path == _PATH_ORDER:
            return _order_ok()
        raise AssertionError(f"unexpected path: {path}")
    return _transport


def _client(transport) -> OKXSpotExecutionClient:
    return OKXSpotExecutionClient(
        EventBus(), signer=object(), rest_base_url="https://x",
        symbol="BTC-USDT", filters=_FILTERS, base_asset="BTC",
        transport=transport, public_transport=lambda *a, **k: {},
    )


# ---------------------------------------------------------------------------
# _resolve_td_mode unit tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("acct_lv", ["1", "2"])
def test_resolve_td_mode_cash_for_spot_modes(acct_lv):
    c = _client(_make_transport(acct_lv))
    assert c._resolve_td_mode() == "cash"


@pytest.mark.parametrize("acct_lv", ["3", "4"])
def test_resolve_td_mode_cross_for_margin_modes(acct_lv):
    c = _client(_make_transport(acct_lv))
    assert c._resolve_td_mode() == "cross"


def test_resolve_td_mode_caches_after_first_call():
    call_count = 0

    def _transport(base, path, method, params, signer, **kw):
        nonlocal call_count
        if path == _PATH_CONFIG:
            call_count += 1
            return _config_resp("3")
        return _order_ok()

    c = _client(_transport)
    assert c._resolve_td_mode() == "cross"
    assert c._resolve_td_mode() == "cross"
    assert c._resolve_td_mode() == "cross"
    assert call_count == 1, f"config fetched {call_count} times — must be cached after first"


def test_resolve_td_mode_falls_back_to_cash_on_error():
    def _transport(base, path, method, params, signer, **kw):
        if path == _PATH_CONFIG:
            raise RuntimeError("network down")
        return _order_ok()

    c = _client(_transport)
    assert c._resolve_td_mode() == "cash"


def test_resolve_td_mode_falls_back_to_cash_on_missing_field():
    def _transport(base, path, method, params, signer, **kw):
        if path == _PATH_CONFIG:
            return {"code": "0", "data": [{"uid": "12345"}]}  # no acctLv key
        return _order_ok()

    c = _client(_transport)
    assert c._resolve_td_mode() == "cash"


# ---------------------------------------------------------------------------
# build_order_params integration: tdMode flows into the submitted params
# ---------------------------------------------------------------------------

def test_build_order_params_tdmode_cross_for_acct_lv_3():
    captured = {}

    def _transport(base, path, method, params, signer, **kw):
        if path == _PATH_CONFIG:
            return _config_resp("3")
        if path == _PATH_ORDER:
            captured["params"] = params
            return _order_ok()
        raise AssertionError(path)

    c = _client(_transport)
    c.submit(_limit_req())
    assert captured["params"]["tdMode"] == "cross"


def test_build_order_params_tdmode_cash_for_acct_lv_1():
    captured = {}

    def _transport(base, path, method, params, signer, **kw):
        if path == _PATH_CONFIG:
            return _config_resp("1")
        if path == _PATH_ORDER:
            captured["params"] = params
            return _order_ok()
        raise AssertionError(path)

    c = _client(_transport)
    c.submit(_limit_req())
    assert captured["params"]["tdMode"] == "cash"


def test_build_order_params_tdmode_cash_for_acct_lv_2():
    captured = {}

    def _transport(base, path, method, params, signer, **kw):
        if path == _PATH_CONFIG:
            return _config_resp("2")
        if path == _PATH_ORDER:
            captured["params"] = params
            return _order_ok()
        raise AssertionError(path)

    c = _client(_transport)
    c.submit(_limit_req())
    assert captured["params"]["tdMode"] == "cash"


def test_build_order_params_tdmode_cross_for_acct_lv_4():
    captured = {}

    def _transport(base, path, method, params, signer, **kw):
        if path == _PATH_CONFIG:
            return _config_resp("4")
        if path == _PATH_ORDER:
            captured["params"] = params
            return _order_ok()
        raise AssertionError(path)

    c = _client(_transport)
    c.submit(_limit_req())
    assert captured["params"]["tdMode"] == "cross"
