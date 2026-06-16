"""Regression guards for the RESIDUAL native teardown crashes (Windows 0xC0000409 / access
violation / "worker crashed") that survived the ADS auto-hide removal.

WHY SUBPROCESS: these are *native* crashes — a still-running ``QThread`` destroyed at C++
teardown, or a dangling ADS components-factory dereferenced during deferred manager teardown.
They abort the whole interpreter (``qFatal`` → ``0xC0000409`` / an access violation), so an
in-process ``assert`` can never observe them: the process is already dead. The only way to make
them a deterministic, catchable signal is to spawn a CHILD interpreter, run the minimal pattern
for a few cycles, and assert on the child's EXIT CODE (0 == clean, nonzero/abort == crash).

WHAT WAS CHARACTERIZED (the residual crash is NOT auto-hide):
  * The "calendar" tool (``EconomicCalendarTab`` + ``CalendarSpace``'s equity tabs) starts
    network-fetch ``QThread`` workers on construction/show. The ONLY join is wired to
    ``QApplication.aboutToQuit`` (``_stop_workers``). Closing the tool window or tearing down the
    ``MainWindow`` does NOT join them, and in the GUI suite the module-scoped ``QApplication``
    never quits — so a still-running worker is freed at interpreter exit → ``~QThread`` qFatal →
    ``0xC0000409``. This is the rare ~1/60 flake in
    ``test_workspace_gui.py::test_each_tool_opens_and_closes_as_window`` (opening every tool opens
    calendar; the crash was always the calendar, the other 7 tools were red herrings).
    PROVEN: neutering the workers' ``run()`` (no thread runs) → clean exit; calling
    ``_stop_workers()`` before destroy → clean exit; both confirmed in this repo.
  * A custom ``CDockComponentsFactory`` installed via ``mgr.setComponentsFactory(Factory())`` as
    an UNOWNED temporary: ADS keeps a non-owning C++ pointer, Python frees the temporary, ADS
    later dereferences the dangle while vending/tearing down dock-area title bars → access
    violation. The app's fix is to RETAIN the factory ref for the manager's lifetime (app.py
    already does ``self._dock_factory = ...``); these tests guard that the retained-ref pattern
    stays clean (a regression that drops the ref re-introduces the UAF).

These tests are marked ``teardown_repro`` + ``slow`` so they can be excluded from a flaky parallel
run with ``-m "not teardown_repro"`` (each child spins its own ``QApplication`` and takes ~1-3s).
Upstream tracking: mborgerson/pyside6_qtads#31 (ADS C++ teardown footguns).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import subprocess
import sys
import textwrap

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("PySide6QtAds")

import PySide6QtAds as QtAds  # noqa: E402
from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.ui.dockshell import SpaceDeck  # noqa: E402

pytestmark = [pytest.mark.teardown_repro, pytest.mark.slow]

# Windows abort code for a Qt qFatal (e.g. ~QThread "Destroyed while thread is still running").
_STATUS_FATAL = 0xC0000409          # also surfaces as a signed -1073740791
_STATUS_AV = 0xC0000005             # access violation (the components-factory UAF flavour)


def _run_child(body: str, tmp_path, *, timeout: int = 60) -> int:
    """Write *body* to a temp .py, run it with THIS interpreter offscreen, return the exit code.

    The child is fully self-contained (it builds its own ``QApplication``). We return the raw
    returncode so callers can distinguish a clean 0 from a native abort (large/negative code).
    """
    script = tmp_path / "child.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["VIKE_DISABLE_LIVE"] = "1"
    env["VIKE_DISABLE_SESSION"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode


def _is_native_crash(code: int) -> bool:
    """True if *code* looks like a native abort (0xC0000409 / access violation), not a clean exit
    or an ordinary Python exception (1)."""
    masked = code & 0xFFFFFFFF
    return code != 0 and (masked in (_STATUS_FATAL, _STATUS_AV) or code < -1)


# A blocking stub repo makes the worker GUARANTEED still-running when we tear the tab down —
# zero network, 100% deterministic (no timing luck). Shared by the mitigated + raw children.
_STUB_REPO_AND_TAB = """
    import os, sys, gc, time
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import faulthandler; faulthandler.enable()
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    from vike_trader_app.ui.economic_calendar import EconomicCalendarTab

    class _SlowRepo:
        # get_week blocks so the fetch QThread is still mid-run at teardown (no network).
        def get_week(self, ws, force=False):
            time.sleep(3.0)
            return []
"""


def test_calendar_worker_stopped_before_teardown_exits_clean(tmp_path):
    """MITIGATED pattern: a still-running calendar fetch QThread that is JOINED (``_stop_workers``)
    before the tab is destroyed must exit cleanly. This is the fix the app must apply on tool/window
    close (today it only joins on ``QApplication.aboutToQuit``). A regression that destroys the tab
    without joining its worker would flip this child to a native abort and FAIL the test."""
    code = _run_child(
        _STUB_REPO_AND_TAB + """
    tab = EconomicCalendarTab(repository=_SlowRepo())
    tab.show()
    app.processEvents()                # showEvent -> refresh_async -> _start_worker (QThread.start)
    tab._stop_workers()                # the missing close-time join: wait/terminate the worker
    app.processEvents()
    tab.deleteLater()
    app.processEvents()
    gc.collect()
    app.processEvents()
    print("CLEAN", flush=True)
    """,
        tmp_path,
    )
    assert code == 0, (
        f"joining the calendar fetch worker before destroy should exit clean, got {code} "
        f"(0x{code & 0xFFFFFFFF:08X}) — a regression dropped the _stop_workers join"
    )


def test_calendar_space_workers_stopped_before_teardown_exits_clean(tmp_path):
    """Same mitigation, the FULL ``CalendarSpace`` (economic + earnings + dividends + ipo = four
    fetch QThreads — the actual widget the 'calendar' tool builds). Joining every contained tab's
    workers before destroy must exit clean. (Network-free: equity fetches resolve fast/empty
    offscreen; the economic tab uses the blocking stub so at least one worker is provably running.)"""
    code = _run_child(
        _STUB_REPO_AND_TAB + """
    from vike_trader_app.ui.equity_calendar import CalendarSpace
    space = CalendarSpace(economic_tab=EconomicCalendarTab(repository=_SlowRepo()))
    space.show()
    app.processEvents()
    # join every contained tab's workers (what a correct tool-close teardown must do)
    for tab in (space.economic, space.earnings, space.dividends, space.ipo):
        tab._stop_workers()
    app.processEvents()
    space.deleteLater()
    app.processEvents()
    gc.collect()
    app.processEvents()
    print("CLEAN", flush=True)
    """,
        tmp_path,
    )
    assert code == 0, (
        f"joining every CalendarSpace worker before destroy should exit clean, got {code} "
        f"(0x{code & 0xFFFFFFFF:08X})"
    )


@pytest.mark.xfail(
    reason="RAW residual crash: a still-running calendar fetch QThread destroyed at C++ teardown "
    "trips ~QThread qFatal (0xC0000409). NO in-app mitigation removes it short of joining the "
    "worker on close (covered by the passing tests above) or an upstream Qt/ADS fix "
    "(mborgerson/pyside6_qtads#31). strict=False: if Qt/ADS ever stops aborting here, this child "
    "exits clean and the test XPASSes — a green signal that the footgun is gone.",
    strict=False,
)
def test_calendar_worker_running_at_teardown_crashes(tmp_path):
    """Documents the underlying Qt footgun, GATE-INDEPENDENTLY: construct the fetch QThread DIRECTLY
    (bypassing _start_worker, so the VIKE_DISABLE_LIVE gate can't mask it), start it on a blocking
    stub repo, then drop the only ref while it's mid-run() — a QThread destroyed while running trips
    ~QThread qFatal (0xC0000409). Asserts a clean exit, which FAILS → xfail (strict=False): if Qt ever
    stops aborting here the footgun itself is gone and this XPASSes. (Our app no longer hits this in
    practice — the gate stops the worker starting headless, and CalendarSpace.stop_feed joins it on
    close — guarded by the two passing tests below.)"""
    code = _run_child(
        _STUB_REPO_AND_TAB + """
    import time as _t
    from vike_trader_app.ui.economic_calendar import _CalendarFetchWorker
    w = _CalendarFetchWorker(_SlowRepo(), int(_t.time() * 1000), force=False)
    w.start()
    app.processEvents()                # worker now sleeping in run()
    del w; gc.collect()                # drop the ref WITHOUT join -> running QThread freed
    app.processEvents()
    print("REACHED-END (crash, if any, is at the QThread destructor)", flush=True)
    """,
        tmp_path,
    )
    assert not _is_native_crash(code), (
        f"calendar worker destroyed mid-run aborted the process: {code} (0x{code & 0xFFFFFFFF:08X})"
    )


def test_calendar_gate_blocks_live_worker_under_disable_live(tmp_path):
    """Regression guard for the FIX: under VIKE_DISABLE_LIVE the calendar's DEFAULT (live-network)
    fetch QThread must NOT start on show — so there's nothing to destroy mid-run and teardown is
    clean. (An INJECTED test repo is not 'live' and its worker still runs — that's the load tests;
    only the default-repo live fetch is gated.) A regression dropping the gate starts the live worker
    and crashes here."""
    code = _run_child(
        """
    import os, sys, gc
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    from vike_trader_app.ui.economic_calendar import EconomicCalendarTab
    tab = EconomicCalendarTab()                 # DEFAULT (live) repo — the gated case
    tab.show(); app.processEvents()
    assert not tab._workers, "gate regressed: a live fetch worker started under VIKE_DISABLE_LIVE"
    tab.deleteLater(); app.processEvents(); gc.collect(); app.processEvents()
    print("CLEAN", flush=True)
    """,
        tmp_path,
    )
    assert code == 0, f"calendar teardown under VIKE_DISABLE_LIVE was not clean: {code} (0x{code & 0xFFFFFFFF:08X})"


def test_ads_components_factory_retained_ref_exits_clean(tmp_path):
    """MITIGATED pattern for the components-factory UAF: a custom ``CDockComponentsFactory`` whose
    Python instance is RETAINED for the manager's lifetime (what app.py does via
    ``self._dock_factory``) must survive deferred manager teardown across many cycles. A regression
    that installs the factory as an unowned temporary re-introduces the dangling-pointer
    dereference → access violation, flipping this child to a native abort."""
    code = _run_child(
        """
    import os, sys, gc
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import faulthandler; faulthandler.enable()
    from PySide6 import QtWidgets
    import PySide6QtAds as QtAds
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    class Factory(QtAds.CDockComponentsFactory):
        def createDockAreaTitleBar(self, area):   # noqa: N802 - ADS naming
            return QtAds.CDockAreaTitleBar(area)

    _kept = []   # retain the factory for each manager's lifetime — the fix

    def cycle():
        win = QtWidgets.QMainWindow(); win.resize(1200, 800)
        mgr = QtAds.CDockManager(win)
        f = Factory(); _kept.append(f)            # RETAINED ref -> ADS pointer never dangles
        mgr.setComponentsFactory(f)
        prev = None
        for a in range(4):                        # >= 2 areas so the factory vends >= 2 title bars
            d = QtAds.CDockWidget(mgr, "d%d" % a); d.setWidget(QtWidgets.QLabel("w"))
            mgr.addDockWidget(QtAds.CenterDockWidgetArea if prev is None
                              else QtAds.RightDockWidgetArea, d)
            prev = d
        win.show(); app.processEvents()
        mgr.deleteLater(); win.deleteLater(); app.processEvents(); gc.collect()

    for _ in range(40):
        cycle()
    print("CLEAN", flush=True)
    """,
        tmp_path,
    )
    assert code == 0, (
        f"a retained components-factory should survive teardown cleanly, got {code} "
        f"(0x{code & 0xFFFFFFFF:08X}) — a regression installed it as an unowned temporary (UAF)"
    )


@pytest.mark.xfail(
    reason="RAW components-factory UAF: a custom CDockComponentsFactory installed as an UNOWNED "
    "temporary leaves ADS holding a dangling non-owning pointer it later dereferences while "
    "vending/tearing down >= 2 dock-area title bars -> access violation. Mitigation = retain the "
    "factory ref (covered by the passing test above). strict=False: XPASSes if the binding ever "
    "takes ownership (upstream fix). Note: PySide6 may swallow the dangle on some builds, so this "
    "can pass spuriously — hence strict=False and the retained-ref test is the real guard.",
    strict=False,
)
def test_ads_components_factory_unowned_temporary_crashes(tmp_path):
    """Documents the unmitigated factory UAF: install the factory as a bare temporary (no retained
    ref) with >= 2 dock areas + deferred teardown. Asserts a clean exit; currently the dangling
    pointer aborts → xfail. XPASS only when the binding owns the factory."""
    code = _run_child(
        """
    import os, sys, gc
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import faulthandler; faulthandler.enable()
    from PySide6 import QtWidgets
    import PySide6QtAds as QtAds
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    class Factory(QtAds.CDockComponentsFactory):
        def createDockAreaTitleBar(self, area):   # noqa: N802
            return QtAds.CDockAreaTitleBar(area)

    def cycle():
        win = QtWidgets.QMainWindow(); win.resize(1200, 800)
        mgr = QtAds.CDockManager(win)
        mgr.setComponentsFactory(Factory())       # UNOWNED temporary -> dangling pointer in ADS
        prev = None
        for a in range(4):
            d = QtAds.CDockWidget(mgr, "d%d" % a); d.setWidget(QtWidgets.QLabel("w"))
            mgr.addDockWidget(QtAds.CenterDockWidgetArea if prev is None
                              else QtAds.RightDockWidgetArea, d)
            prev = d
        win.show(); app.processEvents()
        mgr.deleteLater(); win.deleteLater(); app.processEvents(); gc.collect()

    for _ in range(40):
        cycle()
    print("REACHED-END", flush=True)
    """,
        tmp_path,
    )
    assert not _is_native_crash(code), (
        f"unowned components-factory aborted the process: {code} (0x{code & 0xFFFFFFFF:08X})"
    )


# --------------------------------------------------------------------------------------------------
# Fast IN-PROCESS unit test (no subprocess): the #184 arrange_docks try/except guard.
# A deleted CDockWidget passed to arrange_docks must be treated as dead, not raise
# ``RuntimeError: CDockWidget already deleted``. This is the single-process-observable half of the
# residual teardown class (the deferred-deletion race that frees a dock between collection and
# arrange) — it raises a Python RuntimeError (catchable), unlike the native QThread/factory crashes.
# --------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_arrange_docks_skips_deleted_dock_without_raising(app):
    """SpaceDeck.arrange_docks must NOT raise when a dock in the list has had its C++ object freed
    (the teardown race). Build two real docks, force-delete one (so ``isClosed()``/``widget()``
    raise ``RuntimeError: CDockWidget already deleted``), and assert arrange_docks treats it as dead
    and arranges only the survivor — guarding the #184 try/except in ``SpaceDeck.arrange_docks``."""
    host = QtWidgets.QMainWindow()
    mgr = QtAds.CDockManager(host)
    deck = SpaceDeck(mgr)
    a = QtAds.CDockWidget(mgr, "alive")
    a.setWidget(QtWidgets.QLabel("a"))
    b = QtAds.CDockWidget(mgr, "dead")
    b.setWidget(QtWidgets.QLabel("b"))
    mgr.addDockWidget(QtAds.CenterDockWidgetArea, a)
    mgr.addDockWidget(QtAds.CenterDockWidgetArea, b)
    QtWidgets.QApplication.processEvents()

    # Free b's C++ object NOW. After this, b.isClosed()/b.widget() raise RuntimeError — exactly the
    # state the teardown race leaves a dock in between collection and arrange.
    import shiboken6

    shiboken6.delete(b)

    with pytest.raises(RuntimeError):     # confirm the dock is truly a freed C++ object
        b.isClosed()

    # The whole point of the #184 guard: this must NOT raise (a raising dock => treated as dead).
    n = deck.arrange_docks([a, b], "grid")
    assert n == 1                          # only the live dock 'a' was arranged
    host.deleteLater()
    QtWidgets.QApplication.processEvents()
