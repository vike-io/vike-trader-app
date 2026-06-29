"""Out-of-process sandbox: subprocess + timeout boundary."""

import sys

import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.core.sandbox import _child_env, _posix_confine, run_sandboxed
from vike_trader_app.tester import TesterConfig


def _bars(n=6):
    return [Bar(ts=i * 60_000, open=100.0 + i, high=101.0 + i, low=99.0 + i, close=100.0 + i)
            for i in range(n)]


_BUYHOLD = """
from vike_trader_app.core.strategy import SingleSymbolStrategy as Strategy

class S(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
"""

_HANG = """
from vike_trader_app.core.strategy import SingleSymbolStrategy as Strategy

class S(Strategy):
    def on_bar(self, bar):
        while True:
            pass
"""

_RAISES = """
from vike_trader_app.core.strategy import SingleSymbolStrategy as Strategy

class S(Strategy):
    def on_bar(self, bar):
        raise RuntimeError("boom")
"""


def test_valid_strategy_runs_sandboxed():
    res = run_sandboxed(_BUYHOLD, _bars(), TesterConfig(taker_fee=0.0))
    assert res["ok"] is True
    assert res["report"]["n_trades"] >= 0
    assert "total_return" in res["report"]


def test_hanging_strategy_times_out():
    res = run_sandboxed(_HANG, _bars(), TesterConfig(), timeout=3.0)
    assert res["ok"] is False
    assert res["error"] == "timeout"


def test_raising_strategy_reported_not_crash():
    res = run_sandboxed(_RAISES, _bars(), TesterConfig())
    assert res["ok"] is False
    assert "boom" in res["error"] or "RuntimeError" in res["error"]


def test_malicious_source_rejected_in_child():
    bad = ("import os\nfrom vike_trader_app.core.strategy import Strategy\n"
           "class S(Strategy):\n    def on_bar(self, bar): pass\n")
    res = run_sandboxed(bad, _bars(), TesterConfig())
    assert res["ok"] is False
    assert "pre-flight" in res["error"] or "not allowed" in res["error"]


def test_child_env_excludes_secrets_keeps_essentials(monkeypatch):
    """The sandbox child must NOT inherit API keys/tokens (a strategy could read os.environ and
    egress them), but MUST keep what CPython needs to start. (test_valid_strategy_runs_sandboxed
    above already proves the child still imports + runs under this scrubbed env.)"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "SECRET-must-not-leak")
    monkeypatch.setenv("VIKE_SOME_TOKEN", "also-secret")
    env = _child_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "VIKE_SOME_TOKEN" not in env
    assert "PATH" in env                       # essentials retained so the child can start + import


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX confinement preexec is not used on Windows")
def test_posix_confine_returns_a_callable():
    assert callable(_posix_confine())          # resource is present on POSIX -> a preexec_fn


@pytest.mark.skipif(sys.platform == "win32",
                    reason="on Windows apply_child_hardening() LOWERS this process to Low IL — "
                           "must only run in the sandbox child, tested via subprocess below")
def test_apply_child_hardening_is_safe_no_op_off_windows():
    """Off Windows the in-child hardening imports + runs without raising (a safe no-op on macOS /
    on Linux without a libseccomp binding). The valid-strategy test proves the runner still works
    with the hardening call wired in. NEVER call it in-process on Windows: it drops the caller to
    Low integrity (so the pytest process could no longer write .pytest_cache etc.)."""
    from vike_trader_app.core.sandbox.harden import apply_child_hardening

    assert apply_child_hardening() is None


@pytest.mark.skipif(sys.platform != "win32", reason="Low-integrity confinement is Windows-only")
def test_windows_low_integrity_blocks_file_writes():
    """In a CHILD process (never the test process — it would stay Low IL), apply_child_hardening()
    drops to Low integrity and a write to a medium-IL location (the repo) is then blocked, while a
    control child WITHOUT hardening writes fine. This is the #192 file-write RCE mitigation."""
    import os
    import subprocess

    target = os.path.join(os.getcwd(), "_il_test_probe.txt")
    control = subprocess.run([sys.executable, "-c", f"open(r'{target}','w').write('x'); print('WROTE')"],
                             capture_output=True, text=True)
    assert "WROTE" in control.stdout
    if os.path.exists(target):
        os.remove(target)
    hardened = subprocess.run(
        [sys.executable, "-c",
         "from vike_trader_app.core.sandbox.harden import apply_child_hardening as h; h()\n"
         f"\nopen(r'{target}','w').write('x')"],
        capture_output=True, text=True)
    assert hardened.returncode != 0
    assert "PermissionError" in hardened.stderr
    assert not os.path.exists(target)


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object process cap is Windows-only")
def test_windows_process_count_cap_blocks_extra_spawn():
    """With the live-process cap, the venv launcher (stub + real interpreter = 2) runs, but a child
    attempting to spawn a 3rd process is blocked ('Not enough quota'). Assigns the job while the
    child is SUSPENDED (the deterministic seam) then resumes — mirrors _run_confined_windows."""
    import subprocess

    from vike_trader_app.core.sandbox import winjob

    create_suspended = 0x00000004
    child = ("import subprocess,sys\n"
             "try:\n"
             "    subprocess.run([sys.executable,'-c','pass'],capture_output=True,timeout=10)\n"
             "    print('SPAWNED')\n"
             "except OSError:\n"
             "    print('BLOCKED')\n")
    proc = subprocess.Popen([sys.executable, "-c", child], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, creationflags=create_suspended)
    job = winjob.create_job(active_processes=2)
    assert job
    winjob.assign(job, int(proc._handle))
    assert winjob.resume_process(int(proc._handle))
    out, _ = proc.communicate(timeout=30)
    winjob.close_job(job)
    assert out.strip() == "BLOCKED"


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object confinement is Windows-only")
def test_windows_job_object_creates_and_does_not_break_runs():
    """The Windows Job Object must be creatable AND must not break a normal sandbox run (the venv
    launcher needs to spawn the real interpreter — a process-count cap would deadlock that, so we
    only cap memory + UI + kill-on-close). Guards the 'Not enough quota' regression."""
    from vike_trader_app.core.sandbox import winjob

    job = winjob.create_job()
    assert job, "Job Object should be creatable on Windows"
    winjob.close_job(job)
    # creating + closing a standalone job must NOT poison subsequent confined runs
    res = run_sandboxed(_BUYHOLD, _bars(), TesterConfig(taker_fee=0.0))
    assert res["ok"] is True
    assert res["report"]["n_trades"] >= 0


@pytest.mark.skipif(sys.platform != "linux", reason="rlimit + network-namespace preexec runs only on Linux")
def test_confined_strategy_still_runs_on_linux():
    """The rlimits + fresh network namespace must NOT break a normal compute strategy. (Linux-gated;
    authored on Windows so unverified there — this is the guard that runs when Linux CI returns.)"""
    res = run_sandboxed(_BUYHOLD, _bars(), TesterConfig(taker_fee=0.0))
    assert res["ok"] is True
    assert res["report"]["n_trades"] >= 0
