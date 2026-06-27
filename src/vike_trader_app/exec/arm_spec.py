"""Qt-free execution-arm selection model. Bridges the env-var path and the UI selector path so
both converge on one normalized value object consumed by MainWindow._maybe_start_live_exec."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecArmSpec:
    venue: str
    environment: str
    product: str
    symbol: str
    leverage: float


def _pick(value, env_key: str, env: Mapping[str, str]):
    return value if value is not None else env.get(env_key)


def resolve_arm_spec(*, venue, environment, product, symbol, leverage,
                     env: Mapping[str, str] | None = None) -> ExecArmSpec | None:
    env = os.environ if env is None else env
    venue = (_pick(venue, "VIKE_EXEC_VENUE", env) or "").strip().lower()
    environment = (_pick(environment, "VIKE_EXEC_ENV", env) or "").strip().upper()
    if not venue or not environment:
        return None
    product = (_pick(product, "VIKE_EXEC_PRODUCT", env) or "spot").strip().lower()
    if product not in ("spot", "perp"):
        product = "spot"
    raw_lev = _pick(leverage, "VIKE_EXEC_LEVERAGE", env)
    try:
        lev = float(raw_lev) if raw_lev is not None else 1.0
    except (TypeError, ValueError):
        lev = 1.0
    if product == "spot" or lev < 1.0:
        lev = 1.0
    return ExecArmSpec(venue=venue, environment=environment, product=product,
                       symbol=str(symbol), leverage=lev)
