"""Windows Job Object confinement for the sandbox child (issue #192).

On Windows there's no fork/preexec seam, so confinement is applied from the PARENT: create a Job
Object, assign the freshly-spawned child to it, and let the kernel enforce the limits. The child
blocks on ``stdin.read()`` until the parent feeds it the job, so assigning the job *before* writing
stdin means the child is confined before it runs any strategy code.

Limits applied (``create_job``):
  * PROCESS_MEMORY cap  — analogous to POSIX RLIMIT_AS; the child (and any descendant) is bounded.
  * KILL_ON_JOB_CLOSE   — closing the job handle (incl. on parent crash) kills the whole child tree,
    so a hung/leaked child can't outlive the run.
  * UI restrictions      — no clipboard read/write, global atoms, desktop switch, handle inheritance,
    USER handle access, system-parameter or display changes: shrinks the GUI/IPC attack surface.

NOT applied: ACTIVE_PROCESS_LIMIT. The venv ``python.exe`` is a launcher STUB that must spawn the
real interpreter as a subprocess, so a process-count cap of 1 makes the launch fail with "Not enough
quota" (and, given the assign-after-spawn race, does so unpredictably). Re-adding a process-count
cap needs CREATE_SUSPENDED + assign + ResumeThread (subprocess.Popen closes the thread handle, so it
can't resume) — left as a follow-up. Memory/kill-on-close/UI apply reliably and DON'T conflict.

Everything is best-effort and guarded: any Win32 failure returns None / False and the caller falls
back to the env scrub + wall-clock timeout. This does NOT stop file writes — that needs a low/
restricted-integrity token (the remaining #192 sub-item); the Job Object closes the runaway-memory,
process-cleanup, and UI/IPC vectors.
"""

import sys

# JOBOBJECT_BASIC_LIMIT_INFORMATION.LimitFlags
_JOB_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
# SetInformationJobObject info classes
_JobObjectExtendedLimitInformation = 9
_JobObjectBasicUIRestrictions = 4
# JOBOBJECT_BASIC_UI_RESTRICTIONS.UIRestrictionsClass flags
_UILIMIT = (0x0001 | 0x0002 | 0x0004 | 0x0008 | 0x0010 | 0x0020 | 0x0040 | 0x0080)
# HANDLES | READCLIPBOARD | WRITECLIPBOARD | SYSTEMPARAMETERS | DISPLAYSETTINGS | GLOBALATOMS |
# DESKTOP | EXITWINDOWS


def _structs():
    import ctypes
    from ctypes import wintypes

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                    ("WriteOperationCount", ctypes.c_ulonglong),
                    ("OtherOperationCount", ctypes.c_ulonglong),
                    ("ReadTransferCount", ctypes.c_ulonglong),
                    ("WriteTransferCount", ctypes.c_ulonglong),
                    ("OtherTransferCount", ctypes.c_ulonglong)]

    class _BASIC_LIMIT(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                    ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.POINTER(wintypes.ULONG)),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _EXT_LIMIT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BASIC_LIMIT),
                    ("IoInfo", _IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class _UI_RESTRICTIONS(ctypes.Structure):
        _fields_ = [("UIRestrictionsClass", wintypes.DWORD)]

    return _EXT_LIMIT, _UI_RESTRICTIONS


def create_job(memory_bytes: int = 1024 ** 3):
    """Create + configure a confining Job Object; return its handle (int) or None on any failure."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                ctypes.c_void_p, wintypes.DWORD]
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        ext_cls, ui_cls = _structs()
        ext = ext_cls()
        ext.BasicLimitInformation.LimitFlags = (
            _JOB_LIMIT_PROCESS_MEMORY | _JOB_LIMIT_KILL_ON_JOB_CLOSE)
        ext.ProcessMemoryLimit = memory_bytes
        ok = k32.SetInformationJobObject(job, _JobObjectExtendedLimitInformation,
                                         ctypes.byref(ext), ctypes.sizeof(ext))
        ui = ui_cls(UIRestrictionsClass=_UILIMIT)
        k32.SetInformationJobObject(job, _JobObjectBasicUIRestrictions,
                                    ctypes.byref(ui), ctypes.sizeof(ui))
        if not ok:
            k32.CloseHandle(job)
            return None
        return int(job)
    except Exception:  # noqa: BLE001 - confinement is best-effort; degrade to env-scrub + timeout
        return None


def assign(job: int, process_handle: int) -> bool:
    """Assign a running process to the job. Returns False on failure (caller degrades gracefully)."""
    if not job or sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        return bool(k32.AssignProcessToJobObject(job, process_handle))
    except Exception:  # noqa: BLE001
        return False


def close_job(job) -> None:
    """Close the job handle (KILL_ON_JOB_CLOSE then reaps any surviving child). Never raises."""
    if not job or sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(job)
    except Exception:  # noqa: BLE001
        pass
