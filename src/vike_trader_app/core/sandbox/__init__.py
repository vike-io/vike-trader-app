"""sandbox — safe execution of AI-generated strategy source.

``preflight.check_strategy_source`` is a fast in-process AST gate (input hygiene + editor
diagnostics). The actual security boundary is the out-of-process runner (added next): a
separate, hard-killable child process with a wall-clock timeout. Heavy imports stay lazy so
importing ``sandbox.preflight`` is cheap.
"""

import dataclasses
import json
import os
import subprocess
import sys

# The only env vars the sandbox child needs to start CPython + import the package (incl. numpy/polars
# compiled extensions, which need PATH/SYSTEMROOT for DLL resolution on Windows). Everything else —
# crucially every API key / token — is withheld: the child runs untrusted strategy code, so it must
# not be able to read os.environ["ANTHROPIC_API_KEY"] (etc.) and egress it within the timeout.
_CHILD_ENV_KEEP = (
    "PATH", "PYTHONPATH", "PYTHONHOME", "PYTHONIOENCODING", "PYTHONUTF8",
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "TMPDIR", "PATHEXT",
    "LOCALAPPDATA", "APPDATA", "HOMEDRIVE", "HOMEPATH", "HOME", "USERPROFILE",
    "LANG", "LC_ALL", "LC_CTYPE",
)


def _child_env() -> dict:
    """A scrubbed environment for the sandbox child: only the allowlisted vars Python/Windows need,
    with NO secrets carried through. Defence-in-depth — the strategy should never reach os.environ
    (the preflight gate forbids ``os``), but the gate is hygiene, not a boundary."""
    return {k: os.environ[k] for k in _CHILD_ENV_KEEP if k in os.environ}


def _serialize_bars(bars):
    return [[b.ts, b.open, b.high, b.low, b.close, b.volume, b.funding] for b in bars]


def _posix_confine():
    """preexec_fn for the POSIX sandbox child: resource caps + (Linux) network isolation.

    Runs in the forked child BEFORE exec; returns None where ``resource`` is unavailable. EVERY
    control is best-effort and individually guarded — a preexec_fn that RAISES aborts the whole
    spawn ("Exception occurred in preexec_fn"), so per-control failures are swallowed and we fall
    back to the wall-clock timeout (the cross-platform backstop).

    HONESTY: authored on Windows (where this branch never runs) and Linux CI is currently paused,
    so the Linux network isolation below is UNVERIFIED on this machine — it is exercised only by the
    Linux-gated tests (skipped off Linux) when a Linux runner returns. Full filesystem/syscall
    confinement (seccomp-bpf) and the Windows Job-Object/restricted-token backend are the deeper
    follow-ups tracked in issue #192; macOS (sandbox-exec) is not attempted here.
    """
    try:
        import resource
    except ImportError:
        return None

    def _set():
        # Not every limit is enforceable on every POSIX (macOS rejects RLIMIT_AS), so skip the ones
        # that error. RLIMIT_CPU works on macOS too; the wall-clock timeout backstops regardless.
        gb = 1024 ** 3
        for limit, soft_hard in ((resource.RLIMIT_AS, (gb, gb)),      # 1 GB address space
                                 (resource.RLIMIT_CPU, (10, 10))):    # 10 s CPU
            try:
                resource.setrlimit(limit, soft_hard)
            except (ValueError, OSError):
                pass
        # Linux: drop the child into a fresh, EMPTY network namespace (unprivileged, via a user
        # namespace) so untrusted strategy code can't open a socket / egress. Best-effort: if the
        # kernel disallows unprivileged user namespaces, unshare just fails and we degrade to the
        # rlimits + timeout rather than abort the spawn. A compute-only strategy needs no network.
        if sys.platform == "linux":
            try:
                import ctypes
                _CLONE_NEWUSER, _CLONE_NEWNET = 0x10000000, 0x40000000
                ctypes.CDLL("libc.so.6", use_errno=True).unshare(_CLONE_NEWUSER | _CLONE_NEWNET)
            except Exception:  # noqa: BLE001 - any ctypes/kernel hiccup -> fall back, never crash
                pass

    return _set


def run_sandboxed(code, bars, config, *, timeout: float = 30.0) -> dict:
    """Run AI-generated strategy ``code`` over ``bars`` in a separate, hard-killable process.

    Returns ``{"ok": True, "report": {...}}`` or ``{"ok": False, "error": ...}``; NEVER raises on
    child failure/timeout. Confinement layers:
      • all OSes: a hard wall-clock ``timeout`` (kill) + a scrubbed env (no API keys reach the child)
      • POSIX (Linux/macOS): ``_posix_confine`` preexec — RLIMIT_AS/CPU caps, plus on Linux a fresh
        network namespace (no socket/egress). UNVERIFIED here (Linux CI paused) — see _posix_confine.
      • Linux (in-child, see ``harden.apply_child_hardening`` called by the runner): NO_NEW_PRIVS +
        a seccomp denylist (no fork/exec/socket/ptrace) applied before untrusted code runs. seccomp
        can't go in preexec (it would block the child's own exec); best-effort, no-op without a
        libseccomp binding. Also UNVERIFIED on CI (Linux paused).
      • Windows: timeout/env only TODAY — the Job-Object + restricted-token backend is issue #192.
    """
    job = json.dumps({
        "code": code,
        "bars": _serialize_bars(bars),
        "config": dataclasses.asdict(config),
    })
    kwargs = {}
    if sys.platform != "win32":
        confine = _posix_confine()
        if confine is not None:
            kwargs["preexec_fn"] = confine
    try:
        proc = subprocess.run(
            [sys.executable, "-c", "from vike_trader_app.core.sandbox.runner import main; main()"],
            input=job, capture_output=True, text=True, timeout=timeout,
            env=_child_env(),   # scrubbed: no API keys/tokens reach untrusted strategy code
            **kwargs,
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
