"""Threaded options-chain fetch service.

The synchronous `fetch_now()` is used in tests; `refresh()` runs the same provider
call off-thread via a `_FetchWorker` QThread (network I/O — not a Parquet read, so
off-thread is safe). Results/expiries/errors are delivered via Qt signals on the GUI
thread. Never raises into the UI; never shows a modal.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6 import QtCore

from .model import Expiry, OptionChain
from .provider import OptionsProvider, select_provider

# poll cadence by asset class (ms): stocks are ~15-min delayed, so poll lazily
_POLL_MS = {"crypto": 5_000, "equity": 15_000}


class _FetchWorker(QtCore.QThread):
    done = QtCore.Signal(object)   # OptionChain
    fail = QtCore.Signal(str)

    def __init__(self, provider: OptionsProvider, underlying: str, expiry: Expiry,
                 strikes: int | None, parent=None) -> None:
        super().__init__(parent)
        self._p, self._u, self._e, self._s = provider, underlying, expiry, strikes

    def run(self) -> None:
        try:
            self.done.emit(self._p.fetch_chain(self._u, self._e, self._s))
        except Exception as exc:  # noqa: BLE001 - all failures surface via the signal
            self.fail.emit(f"{self._p.name}: {exc}")


class _ExpWorker(QtCore.QThread):
    ok = QtCore.Signal(object)   # list[Expiry]
    err = QtCore.Signal(str)

    def __init__(self, provider: OptionsProvider, underlying: str, parent=None) -> None:
        super().__init__(parent)
        self._provider, self._underlying = provider, underlying

    def run(self) -> None:
        try:
            self.ok.emit(self._provider.list_expiries(self._underlying))
        except Exception as exc:  # noqa: BLE001 - all failures surface via the signal
            self.err.emit(f"{self._provider.name}: {exc}")


class OptionsService(QtCore.QObject):
    chainReady = QtCore.Signal(object)    # OptionChain
    expiriesReady = QtCore.Signal(object)  # list[Expiry]
    failed = QtCore.Signal(str)

    def __init__(self, provider_factory: Callable[[str], OptionsProvider] = select_provider,
                 parent=None) -> None:
        super().__init__(parent)
        self._factory = provider_factory
        self._provider: OptionsProvider | None = None
        self._underlying: str | None = None
        self._expiry: Expiry | None = None
        self._strikes: int | None = 12
        self._busy = False
        self._worker: _FetchWorker | None = None
        self._exp_worker: _ExpWorker | None = None
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.refresh)

    # --- configuration -------------------------------------------------------
    def set_underlying(self, underlying: str) -> None:
        self._underlying = underlying
        self._provider = self._factory(underlying)

    def set_expiry(self, expiry: Expiry) -> None:
        self._expiry = expiry

    def set_strikes(self, n: int | None) -> None:
        self._strikes = n

    # --- expiries (off-thread, fire-and-forget) ------------------------------
    def load_expiries(self) -> None:
        if not (self._provider and self._underlying):
            return
        # latest-wins: drop a prior in-flight worker's signals so it can't deliver
        # stale expiries for a previous underlying (it runs to completion harmlessly).
        if self._exp_worker is not None:
            try:
                self._exp_worker.ok.disconnect()
                self._exp_worker.err.disconnect()
            except (RuntimeError, TypeError):
                pass
        self._exp_worker = _ExpWorker(self._provider, self._underlying, self)
        self._exp_worker.ok.connect(self.expiriesReady.emit)
        self._exp_worker.err.connect(self.failed.emit)
        self._exp_worker.start()

    # --- chain fetch ---------------------------------------------------------
    def fetch_now(self) -> None:
        """Synchronous fetch on the calling thread (used by tests)."""
        if not (self._provider and self._underlying and self._expiry):
            return
        try:
            chain = self._provider.fetch_chain(self._underlying, self._expiry, self._strikes)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{self._provider.name}: {exc}")
            return
        self.chainReady.emit(chain)

    def refresh(self) -> bool:
        """Start an off-thread fetch. Returns False if one is already in flight."""
        if self._busy or not (self._provider and self._underlying and self._expiry):
            return False
        self._busy = True
        self._worker = _FetchWorker(
            self._provider, self._underlying, self._expiry, self._strikes, self
        )
        self._worker.done.connect(self._on_done)
        self._worker.fail.connect(self._on_fail)
        self._worker.finished.connect(self._clear_busy)
        self._worker.start()
        return True

    def _on_done(self, chain: OptionChain) -> None:
        self.chainReady.emit(chain)

    def _on_fail(self, msg: str) -> None:
        self.failed.emit(msg)

    def _clear_busy(self) -> None:
        self._busy = False
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None

    # --- polling -------------------------------------------------------------
    def start_polling(self) -> None:
        ac = self._provider.asset_class if self._provider else "equity"
        self._timer.start(_POLL_MS.get(ac, 15_000))
        self.refresh()

    def stop_polling(self) -> None:
        self._timer.stop()
