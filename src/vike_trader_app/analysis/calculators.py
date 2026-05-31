"""Standalone trading calculators — pure functions for the Tools tab.

Crypto-native mini-utilities (position sizing, liquidation, funding, PnL, expectancy,
risk-of-ruin). No I/O, no Qt — just arithmetic, unit-tested. The Tools UI is a thin shell
over these. Drawdown reuses ``analysis.metrics.max_drawdown``.
"""

from __future__ import annotations

import random


def position_size(account: float, risk_pct: float, entry: float, stop: float) -> dict:
    """Quantity such that hitting ``stop`` from ``entry`` loses ``risk_pct``% of ``account``."""
    risk_amount = account * (risk_pct / 100.0)
    dist = abs(entry - stop)
    qty = risk_amount / dist if dist > 0 else 0.0
    return {"qty": qty, "notional": qty * entry, "risk_amount": risk_amount, "risk_per_unit": dist}


def liquidation_price(entry: float, leverage: float, side: str = "long",
                      maint_margin_pct: float = 0.5) -> float:
    """Isolated-margin liquidation price (linear approximation).

    Long  liq = entry · (1 − 1/lev + mm); Short liq = entry · (1 + 1/lev − mm),
    where mm = maint_margin_pct/100. Returns 0.0 for invalid inputs.
    """
    if leverage <= 0 or entry <= 0:
        return 0.0
    mm = maint_margin_pct / 100.0
    if side.lower().startswith("s"):
        return entry * (1 + 1 / leverage - mm)
    return entry * (1 - 1 / leverage + mm)


def funding_cost(notional: float, funding_rate: float, n_periods: float) -> float:
    """Total perp funding paid (+) / received (−) over ``n_periods`` at ``funding_rate``/period."""
    return notional * funding_rate * n_periods


def trade_pnl(entry: float, exit_: float, qty: float, side: str = "long",
              fee_rate: float = 0.0, funding: float = 0.0) -> dict:
    """Gross / fees / net PnL + return % for one round-trip (spot or perp)."""
    direction = -1.0 if side.lower().startswith("s") else 1.0
    gross = (exit_ - entry) * qty * direction
    fees = (entry + exit_) * qty * fee_rate
    net = gross - fees - funding
    cost_basis = entry * qty
    return {"gross": gross, "fees": fees, "funding": funding, "net": net,
            "return_pct": (net / cost_basis * 100.0) if cost_basis else 0.0}


def expectancy(win_rate: float, avg_win: float, avg_loss: float) -> dict:
    """Expected PnL per trade + profit factor. ``win_rate`` in [0,1]; ``avg_loss`` as a magnitude."""
    wr = max(0.0, min(1.0, win_rate))
    loss = abs(avg_loss)
    exp = wr * avg_win - (1 - wr) * loss
    denom = (1 - wr) * loss
    pf = (wr * avg_win) / denom if denom > 0 else float("inf")
    return {"expectancy": exp, "profit_factor": pf}


def risk_of_ruin(win_rate: float, payoff_ratio: float, risk_pct: float,
                 ruin_drawdown_pct: float = 100.0, max_trades: int = 1000,
                 trials: int = 2000, seed: int = 0) -> float:
    """Monte-Carlo probability of losing ``ruin_drawdown_pct``% of the bankroll within ``max_trades``.

    Classic gambler's-ruin: each trade stakes a FIXED ``risk_pct``% of the starting bankroll,
    winning ``payoff_ratio``× the stake with probability ``win_rate`` (else losing the stake), so
    equity can actually reach the ruin floor. Deterministic for a given ``seed``.
    """
    rng = random.Random(seed)
    stake = risk_pct / 100.0                     # fixed fraction of the starting bankroll
    ruin_level = 1.0 - ruin_drawdown_pct / 100.0  # 0.0 == total ruin
    ruined = 0
    for _ in range(trials):
        equity = 1.0
        for _ in range(max_trades):
            equity += stake * payoff_ratio if rng.random() < win_rate else -stake
            if equity <= ruin_level:
                ruined += 1
                break
    return ruined / trials if trials else 0.0
