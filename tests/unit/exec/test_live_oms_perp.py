"""LiveOmsHub folds FundingEvent->balance and PositionLiquidated->flat+LIQUIDATED FSM."""

from vike_trader_app.exec.accounting import Account
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.events import (
    FillEvent, FundingEvent, OrderRequest, PositionLiquidated,
)
from vike_trader_app.exec.live_oms import LiveOmsHub
from vike_trader_app.exec.order import ManagedOrder, OrderStatus
from vike_trader_app.exec.risk import RiskGate, RiskLimits


class _SpyClient:
    def submit(self, request): pass
    def detach(self): pass


def _hub():
    bus = EventBus()
    return LiveOmsHub(bus=bus, account=Account(), gate=RiskGate(RiskLimits()),
                      client=_SpyClient(), venue="binance", symbol="BTCUSDT")


def test_funding_event_folds_into_balance():
    hub = _hub()
    hub.bus.publish(FundingEvent(venue="binance", symbol="BTCUSDT", position_side="BOTH",
                                 funding_rate=0.0001, amount=-1.25))
    assert hub.account.balance == -1.25


def test_funding_for_other_symbol_ignored():
    hub = _hub()
    hub.bus.publish(FundingEvent(venue="binance", symbol="ETHUSDT", position_side="BOTH",
                                 funding_rate=0.0001, amount=-9.0))
    assert hub.account.balance == 0.0


def test_liquidation_flattens_account_and_advances_fsm():
    hub = _hub()
    req = OrderRequest(client_order_id="o1", venue="binance", symbol="BTCUSDT",
                       side=+1, qty=2.0, order_type="limit", price=100.0)
    hub.registry["o1"] = ManagedOrder(request=req, status=OrderStatus.ACCEPTED)
    hub.bus.publish(FillEvent(trade_id="t0", client_order_id="o1", venue="binance",
                              symbol="BTCUSDT", side=+1, last_qty=2.0, last_px=100.0))
    hub.bus.publish(PositionLiquidated(venue="binance", symbol="BTCUSDT", position_side="BOTH",
                                       qty=2.0, liq_price=60.0, fee=0.5))
    assert hub.account.positions[("binance", "BTCUSDT", "BOTH")]["size"] == 0.0
    assert hub.account.realized_pnl == -80.0
    assert hub.account.balance == -0.5
    assert hub.registry["o1"].status is OrderStatus.LIQUIDATED
