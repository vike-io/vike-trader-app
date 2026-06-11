"""Tests for vike_trader_app.crash — Tier-0 local crash capture + spool/drain.

Qt-free core: path scrubbing (privacy), the crash handler that spools a scrubbed event,
and the next-launch drain that hands spooled crashes to telemetry. No network here.
"""

import json
import logging

import pytest

from vike_trader_app import crash


@pytest.fixture
def logdir(tmp_path, monkeypatch):
    """Point the shared log dir (logging_setup._log_dir) at a tmp dir for this test."""
    monkeypatch.setenv("VIKE_LOG_DIR", str(tmp_path))
    return tmp_path


# --- path scrubbing (no usernames / absolute paths leak off-machine) ------------------

def test_scrub_path_replaces_user_home_with_tilde():
    from pathlib import Path

    out = crash._scrub_path(str(Path.home() / "secret" / "thing.py")).replace("\\", "/")
    assert out.startswith("~/")
    assert "secret/thing.py" in out


def test_scrub_path_strips_site_packages_to_package_relative():
    out = crash._scrub_path("/opt/env/lib/site-packages/vike_trader_app/ui/app.py")
    assert out.replace("\\", "/") == "vike_trader_app/ui/app.py"


def test_scrub_traceback_scrubs_file_lines_but_keeps_message():
    from pathlib import Path

    home = str(Path.home())
    tb = (
        "Traceback (most recent call last):\n"
        f'  File "{home}/proj/m.py", line 3, in <module>\n'
        '    raise ValueError("boom")\n'
        "ValueError: boom"
    )
    out = crash._scrub_traceback(tb)
    assert home.replace("\\", "/") not in out.replace("\\", "/")
    assert "ValueError: boom" in out  # the message body is untouched


# --- _handle: spool one scrubbed event, log CRITICAL, never raise ---------------------

def test_handle_spools_one_scrubbed_event_and_logs_critical(logdir, caplog):
    with caplog.at_level(logging.CRITICAL):
        try:
            raise ValueError("kaboom")
        except ValueError as e:
            crash._handle("python_main", type(e), e, e.__traceback__)

    files = list((logdir / "pending_crash").glob("*.json"))
    assert len(files) == 1
    ev = json.loads(files[0].read_text(encoding="utf-8"))
    assert ev["kind"] == "python_main"
    assert ev["exc_type"] == "ValueError"
    assert "kaboom" in ev["traceback"]
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)


def test_handle_never_raises_when_spool_unwritable(logdir):
    # A file where the spool DIR is expected → mkdir fails; _handle must swallow it.
    (logdir / "pending_crash").write_text("not a dir", encoding="utf-8")
    crash._handle("python_main", ValueError, ValueError("x"), None)  # must not raise


# --- drain_pending: report each spooled crash then delete it --------------------------

def _spool(logdir, name, payload):
    d = logdir / "pending_crash"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(payload), encoding="utf-8")


def test_drain_reports_then_deletes_each_spool_file(logdir, monkeypatch):
    for i in range(3):
        _spool(logdir, f"{i:03d}-x.json", {"kind": "python_main", "exc_type": "E", "traceback": "t"})
    sent = []
    monkeypatch.setattr(crash.telemetry, "report_crash", lambda ev: sent.append(ev))

    n = crash.drain_pending()

    assert n == 3
    assert len(sent) == 3
    assert not list((logdir / "pending_crash").glob("*.json"))  # queue fully drained


def test_drain_caps_at_max_spool_and_clears_the_rest(logdir, monkeypatch):
    for i in range(crash._MAX_SPOOL + 5):
        _spool(logdir, f"{i:03d}-x.json", {"kind": "python_main"})
    sent = []
    monkeypatch.setattr(crash.telemetry, "report_crash", lambda ev: sent.append(ev))

    crash.drain_pending()

    assert len(sent) == crash._MAX_SPOOL  # only the newest _MAX_SPOOL are reported
    assert not list((logdir / "pending_crash").glob("*.json"))  # remainder deleted unreported


def test_drain_converts_nonempty_faulthandler_log_to_native_then_truncates(logdir, monkeypatch):
    (logdir / "faulthandler.log").write_text(
        "Fatal Python error: Segmentation fault\n\n"
        'Current thread 0x1:\n  File "x.py", line 1 in f\n',
        encoding="utf-8",
    )
    sent = []
    monkeypatch.setattr(crash.telemetry, "report_crash", lambda ev: sent.append(ev))

    crash.drain_pending()

    native = [e for e in sent if e["kind"] == "native"]
    assert len(native) == 1
    assert "Segmentation fault" in native[0]["traceback"]
    assert (logdir / "faulthandler.log").read_text(encoding="utf-8") == ""  # rotated


def test_drain_ignores_empty_faulthandler_log(logdir, monkeypatch):
    (logdir / "faulthandler.log").write_text("", encoding="utf-8")
    sent = []
    monkeypatch.setattr(crash.telemetry, "report_crash", lambda ev: sent.append(ev))

    crash.drain_pending()

    assert sent == []


def test_drain_never_raises_when_reporter_throws(logdir, monkeypatch):
    _spool(logdir, "000-x.json", {"kind": "python_main"})

    def boom(ev):
        raise RuntimeError("receiver down")

    monkeypatch.setattr(crash.telemetry, "report_crash", boom)
    crash.drain_pending()  # must not raise
    assert not list((logdir / "pending_crash").glob("*.json"))  # still cleaned up


# --- install: wires hooks, idempotent -------------------------------------------------

def test_install_wires_excepthook_and_is_idempotent(logdir, monkeypatch):
    import sys
    import threading

    monkeypatch.setattr(crash, "_installed", False, raising=False)
    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
    monkeypatch.setattr(threading, "excepthook", threading.__excepthook__)

    crash.install(app_version="9.9.9")
    assert sys.excepthook is not sys.__excepthook__  # our hook is installed
    chosen = sys.excepthook

    crash.install()  # second call is a no-op
    assert sys.excepthook is chosen
