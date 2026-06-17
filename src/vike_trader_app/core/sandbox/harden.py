"""Best-effort in-child syscall hardening for the Linux sandbox (folds into issue #192).

Installed by ``runner.main()`` AFTER the heavy framework imports but BEFORE any untrusted strategy
source is compiled/executed — NOT in the parent's ``preexec_fn``, where blocking ``execve`` would
block the child's OWN exec of the interpreter. Linux-only and fully best-effort: every step is
guarded, so a missing capability degrades to the layers already in place (rlimits + a fresh network
namespace from ``_posix_confine``'s preexec, plus the cross-platform wall-clock timeout) and NEVER
aborts the run.

Layers added here:
  * ``PR_SET_NO_NEW_PRIVS`` — the child can't gain privileges via a setuid binary (also a
    precondition for loading a seccomp filter unprivileged).
  * a seccomp DENYLIST (via the optional ``seccomp`` / ``pyseccomp`` libseccomp binding): process
    creation (fork/vfork/execve/execveat), raw networking (socket/connect/…), and ptrace return
    EPERM. Thread creation (``clone`` for pthreads — numba/numpy) is deliberately NOT blocked.
    Default action ALLOW, so ordinary compute syscalls (mmap/read/write/open) are untouched, and the
    action is EPERM (not KILL) so a stray denied call surfaces as a Python error, not a silent
    SIGSYS death.

HONESTY: Linux CI is paused, so this is UNVERIFIED on CI. It's a no-op without a libseccomp binding
and on non-Linux. The Linux-gated sandbox tests guard against breaking a normal compute strategy
when a Linux runner returns.
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
    """Apply NO_NEW_PRIVS + a seccomp denylist in this (Linux) child. No-op elsewhere; never raises."""
    if sys.platform != "linux":
        return
    _set_no_new_privs()
    _install_seccomp()


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
