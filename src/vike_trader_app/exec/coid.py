"""Session/uuid-prefixed client_order_id minter for live orders.

A bare itertools counter resets on restart and could collide with a still-open prior-session
newClientOrderId; a per-session uuid prefix avoids that. Validated against Binance's charset
^[\\.A-Z\\:/a-z0-9_-]{1,36}$.
"""

from __future__ import annotations

import itertools
import re
import uuid

BINANCE_COID_RE = re.compile(r"^[\.A-Z\:/a-z0-9_-]{1,36}$")


class CoidMinter:
    """Mints `<session>-<n>` ids unique across sessions and valid for Binance newClientOrderId."""

    def __init__(self, *, session: str | None = None) -> None:
        self._session = session or uuid.uuid4().hex[:8]
        self._seq = itertools.count()

    def mint(self) -> str:
        coid = f"{self._session}-{next(self._seq)}"
        if not BINANCE_COID_RE.match(coid):
            raise ValueError(f"client_order_id violates Binance charset: {coid!r}")
        return coid
