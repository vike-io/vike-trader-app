"""Best-effort in-child OS hardening for the sandbox (folds into issue #192).

Installed by ``runner.main()`` AFTER the heavy framework imports but BEFORE any untrusted strategy
source is compiled/executed — so the framework initialises unrestricted and only the untrusted code
runs confined. Cross-platform, fully best-effort: every step is guarded, so a missing capability
degrades to the layers already in place (the parent's rlimits + netns preexec on Linux / Job Object
on Windows, plus the cross-platform env scrub + wall-clock timeout) and NEVER aborts the run.

Linux:
  * ``PR_SET_NO_NEW_PRIVS`` — the child can't gain privileges via a setuid binary (also a
    precondition for loading a seccomp filter unprivileged).
  * a seccomp DENYLIST (via the optional ``seccomp`` / ``pyseccomp`` libseccomp binding): process
    creation (fork/vfork/execve/execveat), raw networking (socket/connect/…), and ptrace return
    EPERM. Thread creation (``clone`` for pthreads — numba/numpy) is deliberately NOT blocked.
    Default action ALLOW; the action is EPERM (not KILL) so a stray denied call is a Python error,
    not a silent SIGSYS death. Needs the binding installed (the ``sandbox`` extra ->
    ``pyseccomp``); no-op without it. VERIFIED on Ubuntu 24.04 (prod1, 2026-06-17): the unprivileged
    ``unshare(user+net)`` netns blocks egress, rlimits/no_new_privs apply, and the seccomp denylist
    blocks ``socket`` with the binding present. CI still does not exercise it (Linux runners paused).

Windows:
  * drop THIS process to LOW integrity (the child lowers its OWN token — Windows allows lowering,
    never raising). Mandatory Integrity Control's "no write up" then blocks the untrusted strategy
    from WRITING to the medium-integrity filesystem (the original #192 RCE was a file write). This
    is done in-child, NOT via CreateProcessAsUser, which is privilege-gated and needs manual pipe
    plumbing. Already-open stdin/stdout pipes keep working (MIC is checked at handle-open, not use),
    and the child receives all its data via stdin, so it needs no file reads. The Job Object set up
    by the parent (winjob) remains in force. CANNOT be undone by the strategy (you can't raise IL).
"""

import sys

# Process-creation, raw-networking, and debugging syscalls untrusted compute code never needs.
# clone/clone3 are intentionally absent (threads use them; blocking them would break numba/numpy).
_DENY = (
    "execve", "execveat", "fork", "vfork", "ptrace",
    "socket", "socketcall", "connect", "bind", "listen", "accept", "accept4",
    "sendto", "sendmsg", "recvfrom", "recvmsg",
)


def apply_child_hardening() -> None:
    """Confine THIS child before it runs untrusted code: Linux -> NO_NEW_PRIVS + seccomp denylist;
    Windows -> drop to Low integrity (no filesystem writes). No-op elsewhere; never raises."""
    if sys.platform == "linux":
        _set_no_new_privs()
        _install_seccomp()
    elif sys.platform == "win32":
        _set_low_integrity_windows()


def _set_no_new_privs() -> None:
    try:
        import ctypes

        pr_set_no_new_privs = 38
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(pr_set_no_new_privs, 1, 0, 0, 0)
    except Exception:  # noqa: BLE001 - hardening is best-effort; degrade rather than abort the run
        pass


def _install_seccomp() -> None:
    try:
        import errno
        try:
            import seccomp as _scmp          # python-libseccomp
        except ImportError:
            import pyseccomp as _scmp         # the maintained fork exposes the same API
    except ImportError:
        return                               # no binding -> rely on netns + rlimits + no_new_privs + timeout
    try:
        flt = _scmp.SyscallFilter(defaction=_scmp.ALLOW)
        for name in _DENY:
            try:
                flt.add_rule(_scmp.ERRNO(errno.EPERM), name)
            except Exception:                # noqa: BLE001 - syscall unknown on this arch -> skip it
                pass
        flt.load()
    except Exception:                        # noqa: BLE001 - any libseccomp hiccup -> degrade, never crash
        pass


def _redirect_temp_to_lowil() -> None:
    """Point TEMP/TMP at a LocalLow scratch dir BEFORE lowering IL, so a low-IL child still has a
    writable temp (LocalLow carries a Low mandatory label). Without this, tempfile finds no usable
    dir (all are medium-IL) and any temp-using code raises. The scratch is the ONLY writable spot —
    the repo / system / profile stay read-only, which is the whole point."""
    import os

    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return
    low_tmp = os.path.join(os.path.dirname(local), "LocalLow", "vike-sandbox-temp")
    try:
        os.makedirs(low_tmp, exist_ok=True)   # created at medium IL; inherits LocalLow's Low label
    except OSError:
        return
    for var in ("TEMP", "TMP", "TMPDIR"):
        os.environ[var] = low_tmp
    import tempfile

    tempfile.tempdir = None                   # force gettempdir() to re-read the env on next use


def _set_low_integrity_windows() -> None:
    """Lower THIS process's token to Low integrity (S-1-16-4096). MIC then blocks writes to the
    medium-integrity filesystem. Best-effort; leaves the IL unchanged on any failure."""
    _redirect_temp_to_lowil()                 # must precede the drop (needs medium IL to create it)
    try:
        import ctypes
        from ctypes import wintypes

        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                            ctypes.POINTER(wintypes.HANDLE)]
        advapi.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
        advapi.GetLengthSid.argtypes = [ctypes.c_void_p]
        advapi.GetLengthSid.restype = wintypes.DWORD
        advapi.SetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                               ctypes.c_void_p, wintypes.DWORD]
        k32.LocalFree.argtypes = [ctypes.c_void_p]

        token_adjust_default, token_query = 0x0080, 0x0008
        token_integrity_level = 25            # TOKEN_INFORMATION_CLASS.TokenIntegrityLevel
        se_group_integrity = 0x00000020

        class _SID_AND_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

        class _TOKEN_MANDATORY_LABEL(ctypes.Structure):
            _fields_ = [("Label", _SID_AND_ATTRIBUTES)]

        htok = wintypes.HANDLE()
        if not advapi.OpenProcessToken(k32.GetCurrentProcess(),
                                       token_adjust_default | token_query, ctypes.byref(htok)):
            return
        psid = ctypes.c_void_p()
        try:
            if not advapi.ConvertStringSidToSidW("S-1-16-4096", ctypes.byref(psid)):
                return
            til = _TOKEN_MANDATORY_LABEL()
            til.Label.Sid = psid
            til.Label.Attributes = se_group_integrity
            size = ctypes.sizeof(til) + advapi.GetLengthSid(psid)
            advapi.SetTokenInformation(htok, token_integrity_level, ctypes.byref(til), size)
        finally:
            if psid:
                k32.LocalFree(psid)
            k32.CloseHandle(htok)
    except Exception:  # noqa: BLE001 - best-effort; degrade to Job Object + env-scrub + timeout
        pass
