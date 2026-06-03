"""Real source logos for the News space, fetched once from a favicon service and cached to disk.

TradingView shows each provider's real logo; our free RSS providers have known domains, so we
pull a clean 64px favicon per domain (Google's s2 service), cache it under storage/news_logos/,
and render it as a small rounded badge. Network runs on a one-shot QThread (safe — no Parquet);
until a logo lands, callers fall back to the colored-initial badge. Never raises on the UI thread.
"""
from __future__ import annotations

import os
import urllib.request
from urllib.parse import urlparse

from PySide6 import QtCore, QtGui

_CACHE_DIR = os.path.join("storage", "news_logos")
_FAVICON = "https://www.google.com/s2/favicons?domain={domain}&sz=64"
_UA = "Mozilla/5.0 (vike-trader-app news logos)"
_TIMEOUT = 5.0


def _domain_of(url: str) -> str:
    net = urlparse(url).netloc.lower()
    return net[4:] if net.startswith("www.") else net


class _LogoFetcher(QtCore.QThread):
    """Downloads missing favicons (domain -> storage/news_logos/<domain>.png), one shot."""

    done = QtCore.Signal()

    def __init__(self, jobs: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self._jobs = jobs          # [(domain, dest_path)]
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        got = False
        for domain, dest in self._jobs:
            if self._stop:
                break
            try:
                req = urllib.request.Request(_FAVICON.format(domain=domain), headers={"User-Agent": _UA})
                with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                    data = resp.read()
                if data and len(data) > 70:          # s2 returns a tiny globe placeholder on miss
                    with open(dest, "wb") as fh:
                        fh.write(data)
                    got = True
            except Exception:  # noqa: BLE001 - a missing logo just means we keep the initials fallback
                continue
        if got and not self._stop:
            self.done.emit()


class LogoStore(QtCore.QObject):
    """source name -> real logo QPixmap (cached) or None. Emits ``updated`` when new logos land."""

    updated = QtCore.Signal()

    def __init__(self, providers, parent=None):
        super().__init__(parent)
        # source display name -> bare domain (e.g. "CoinDesk" -> "coindesk.com")
        self._domain: dict[str, str] = {}
        for spec in providers:
            d = _domain_of(spec.url)
            if d:
                self._domain[spec.name] = d
        self._mem: dict[str, QtGui.QPixmap] = {}      # f"{source}@{size}" -> rounded pixmap
        self._fetcher: _LogoFetcher | None = None
        os.makedirs(_CACHE_DIR, exist_ok=True)

    def _path(self, source: str) -> str | None:
        d = self._domain.get(source)
        return os.path.join(_CACHE_DIR, f"{d}.png") if d else None

    def pixmap(self, source: str, size: int) -> QtGui.QPixmap | None:
        """A rounded logo badge for ``source`` if its favicon is cached, else None (use initials)."""
        key = f"{source}@{size}"
        if key in self._mem:
            return self._mem[key]
        path = self._path(source)
        if not path or not os.path.exists(path):
            return None
        raw = QtGui.QPixmap(path)
        if raw.isNull():
            return None
        pm = self._rounded(raw, size)
        self._mem[key] = pm
        return pm

    @staticmethod
    def _rounded(raw: QtGui.QPixmap, size: int) -> QtGui.QPixmap:
        out = QtGui.QPixmap(size, size)
        out.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(out)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        path = QtGui.QPainterPath()
        path.addRoundedRect(0, 0, size, size, size * 0.28, size * 0.28)
        p.setClipPath(path)
        p.fillRect(0, 0, size, size, QtGui.QColor("#ffffff"))   # so transparent favicons stay legible
        scaled = raw.scaled(size, size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        p.drawPixmap((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
        p.end()
        return out

    def prefetch_async(self) -> None:
        """Kick a one-shot background fetch of every provider's missing favicon."""
        if self._fetcher is not None:
            return
        jobs = []
        seen: set[str] = set()
        for source, domain in self._domain.items():
            path = self._path(source)
            if path and domain not in seen and not os.path.exists(path):
                seen.add(domain)
                jobs.append((domain, path))
        if not jobs:
            return
        self._fetcher = _LogoFetcher(jobs)
        self._fetcher.done.connect(self._on_done)
        self._fetcher.start()

    def _on_done(self) -> None:
        self._mem.clear()        # drop cached fallbacks so freshly-saved logos get loaded
        self.updated.emit()

    def stop(self) -> None:
        if self._fetcher is not None:
            self._fetcher.stop()
            self._fetcher.wait(6000)
            self._fetcher = None
