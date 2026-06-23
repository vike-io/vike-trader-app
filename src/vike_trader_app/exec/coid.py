"""Session/uuid-prefixed client_order_id minter for live orders.

A bare itertools counter resets on restart and could collide with a still-open prior-session
order id; a per-session uuid prefix avoids that. The id is the STRICTEST common denominator across
venues — ALPHANUMERIC only, <=32 chars — so the SAME id is valid as Binance `newClientOrderId`,
Bybit `orderLinkId`, AND OKX `clOrdId` (OKX rejects non-alphanumeric, e.g. a hyphen, with
"Parameter clOrdId error", and caps at 32). No separator: `<8-hex-session><seq>` (the session is a
fixed-width 8-char hex prefix, so concatenation stays unambiguous and unique).
"""

from __future__ import annotations

import itertools
import re
import uuid

CRYPTO_COID_RE = re.compile(r"^[A-Za-z0-9]{1,32}$")  # alphanumeric, <=32: valid on Binance + Bybit + OKX
BINANCE_COID_RE = CRYPTO_COID_RE  # back-compat alias (same compiled pattern)


class CoidMinter:
    """Mints `<8-hex-session><n>` ids — unique across sessions + alphanumeric (valid on all venues)."""

    def __init__(self, *, session: str | None = None) -> None:
        self._session = session or uuid.uuid4().hex[:8]
        self._seq = itertools.count()

    def mint(self) -> str:
        coid = f"{self._session}{next(self._seq)}"
        if not CRYPTO_COID_RE.match(coid):
            raise ValueError(f"client_order_id violates crypto charset: {coid!r}")
        return coid
