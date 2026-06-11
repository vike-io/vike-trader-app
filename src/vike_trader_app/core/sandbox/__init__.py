"""sandbox — safe execution of AI-generated strategy source.

``preflight.check_strategy_source`` is a fast in-process AST gate (input hygiene + editor
diagnostics). The actual security boundary is the out-of-process runner (added next): a
separate, hard-killable child process with a wall-clock timeout. Heavy imports stay lazy so
importing ``sandbox.preflight`` is cheap.
"""

import dataclasses
import json
import subprocess
import sys


def _serialize_bars(bars):
    return [[b.ts, b.open, b.high, b.low, b.close, b.volume, b.funding] for b in bars]


def _posix_limits():
    """A preexec_fn capping address space + CPU on POSIX; None on platforms without ``resource``."""
    try:
        import resource
    except ImportError:
        return None

    def _set():
        # Best-effort per limit: a preexec_fn that RAISES aborts the whole spawn
        # (subprocess.SubprocessError "Exception occurred in preexec_fn"). Not every limit is
        # enforceable on every POSIX — notably macOS rejects RLIMIT_AS — so skip the ones that
        # error rather than killing the sandbox. RLIMIT_CPU (the hard CPU cap) works on macOS too;
        # the wall-clock timeout in run_sandboxed is the cross-platform backstop regardless.
        gb = 1024 ** 3
        for limit, soft_hard in ((resource.RLIMIT_AS, (gb, gb)),      # 1 GB address space
                                 (resource.RLIMIT_CPU, (10, 10))):    # 10 s CPU
            try:
                resource.setrlimit(limit, soft_hard)
            except (ValueError, OSError):
                pass

    return _set


def run_sandboxed(code, bars, config, *, timeout: float = 30.0) -> dict:
    """Run AI-generated strategy ``code`` over ``bars`` in a separate, hard-killable process.

    Returns ``{"ok": True, "report": {...}}`` or ``{"ok": False, "error": ...}``; NEVER raises on
    child failure/timeout. The child process is the SECURITY BOUNDARY (subprocess + wall-clock
    ``timeout`` + POSIX ``setrlimit`` memory/CPU caps). Windows Job-Object caps + a warm-worker pool
    are a documented follow-up; today Windows relies on the timeout alone.
    """
    job = json.dumps({
        "code": code,
        "bars": _serialize_bars(bars),
        "config": dataclasses.asdict(config),
    })
    kwargs = {}
    if sys.platform != "win32":
        limits = _posix_limits()
        if limits is not None:
            kwargs["preexec_fn"] = limits
    try:
        proc = subprocess.run(
            [sys.executable, "-c", "from vike_trader_app.core.sandbox.runner import main; main()"],
            input=job, capture_output=True, text=True, timeout=timeout, **kwargs,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    out = (proc.stdout or "").strip()
    if not out:
        return {"ok": False, "error": "no result", "stderr": (proc.stderr or "")[-2000:]}
    try:
        return json.loads(out.splitlines()[-1])   # result is the LAST json line (strategy may print)
    except json.JSONDecodeError:
        return {"ok": False, "error": "unparseable result", "stdout": out[-2000:]}
