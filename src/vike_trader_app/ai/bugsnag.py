"""Bugsnag integration for the vike-trader DESKTOP app — two independent halves.

1. CRASH FORWARDING (write): turn a scrubbed crash event from :mod:`vike_trader_app.crash`
   (forwarded via :func:`telemetry.report_crash`) into a Bugsnag Error Reporting API payload
   (v5) and POST it to ``notify.bugsnag.com``. Bugsnag has no official Python-desktop notifier,
   so we use the raw API — it slots into the existing urllib-based, opt-in, best-effort pipeline.
   Gated on the ``BUGSNAG_API_KEY`` env var (the client-side notifier key); never raises.

2. TRIAGE (read + resolve): a thin Data Access API client (``api.bugsnag.com``) used to LIST
   open errors, SHOW a stacktrace, and RESOLVE (close) an error after a fix. Needs the SENSITIVE
   ``BUGSNAG_AUTH_TOKEN`` personal token + ``BUGSNAG_PROJECT_ID``. Exposed as a CLI:
   ``python -m vike_trader_app.ai.bugsnag list|show|resolve``.

The pure builders (``parse_python_traceback``, ``crash_event_to_payload``) are unit-tested
without network; the network functions swallow errors (crash path) or surface them (triage CLI).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

_NOTIFY_URL = "https://notify.bugsnag.com/"
_DATA_BASE = "https://api.bugsnag.com"
_NOTIFIER = {"name": "vike-trader", "version": "0.0.0", "url": "https://vike.io"}

# crash "kind" (from crash.py) -> (errorClass fallback, Bugsnag severity)
_KIND_CLASS = {"native": "NativeCrash", "qt_fatal": "QtFatal",
               "python_main": "UnhandledError", "python_thread": "UnhandledThreadError"}


# --- env -------------------------------------------------------------------------------------

def _notifier_key() -> str | None:
    return os.environ.get("BUGSNAG_API_KEY", "").strip() or None


def _release_stage() -> str:
    return os.environ.get("VIKE_RELEASE_STAGE", "production").strip() or "production"


# --- pure payload builders (unit-tested, no network) -----------------------------------------

def parse_python_traceback(text: str) -> list[dict]:
    """Parse a Python traceback into Bugsnag stack frames, INNERMOST first (Bugsnag's order).

    Reads each ``  File "<path>", line <n>, in <method>`` line plus the following source line.
    Tolerant of native-fault dumps (they share the ``File ...`` format); non-matching text is
    ignored. Returns ``[]`` when nothing parses (caller supplies a synthetic frame)."""
    frames: list[dict] = []
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if not s.startswith('File "'):
            continue
        try:
            after = s[len('File "'):]
            path, rest = after.split('"', 1)              # path, then `, line N, in method`
            parts = rest.split(",")
            lineno = int(parts[1].strip().split()[1]) if len(parts) > 1 else 0
            method = parts[2].strip()[3:] if len(parts) > 2 and "in " in parts[2] else "<module>"
        except (ValueError, IndexError):
            continue
        code = lines[i + 1].strip() if i + 1 < len(lines) and not lines[i + 1].strip().startswith('File "') else ""
        frames.append({"file": path, "lineNumber": lineno, "method": method,
                       "code": code or None, "inProject": "vike_trader_app" in path})
    frames.reverse()  # Python prints outermost-first; Bugsnag wants innermost (crash site) first
    return frames


def crash_event_to_payload(event: dict, api_key: str) -> dict:
    """Build a Bugsnag Error Reporting API v5 payload from a scrubbed crash ``event``.

    ``event`` is what :func:`telemetry.report_crash` produced: ``kind``/``exc_type``/
    ``traceback``/``app_version``/``ts_ms``/``client``/``env``. No PII (already scrubbed)."""
    kind = event.get("kind", "python_main")
    tb = event.get("traceback", "") or ""
    error_class = event.get("exc_type") or _KIND_CLASS.get(kind, "Error")
    tb_lines = [ln for ln in tb.splitlines() if ln.strip()]
    message = tb_lines[-1].strip() if tb_lines else kind

    frames = parse_python_traceback(tb)
    if not frames:  # native dump with no parseable Python frames -> one synthetic frame
        frames = [{"file": kind, "lineNumber": 0, "method": kind, "inProject": True}]

    env = event.get("env") or {}
    rt = {}
    if env.get("python"):
        rt["python"] = env["python"]
    if env.get("qt"):
        rt["qt"] = env["qt"]

    return {
        "apiKey": api_key,
        "notifier": dict(_NOTIFIER),
        "events": [{
            "exceptions": [{"errorClass": error_class, "message": message,
                            "stacktrace": frames, "type": "python"}],
            "severity": "error",
            "unhandled": True,
            "severityReason": {"type": "unhandledException"},
            "app": {"version": event.get("app_version"), "releaseStage": _release_stage(),
                    "type": "desktop"},
            "device": {"osName": env.get("platform"), "osVersion": env.get("os"),
                       "runtimeVersions": rt or None},
            "user": {"id": event.get("client")},
            "metaData": {"crash": {"kind": kind, "ts_ms": event.get("ts_ms")}},
        }],
    }


# --- crash forwarding (write; best-effort, never raises) -------------------------------------

def report_crash(event: dict) -> bool:
    """Forward one scrubbed crash event to Bugsnag. No-op (returns False) without an API key.

    Called from :func:`telemetry.report_crash` on the next healthy launch's drain. Best-effort:
    any network/build error is swallowed (the crash pipeline must never fail on reporting)."""
    try:
        key = _notifier_key()
        if not key:
            return False
        payload = crash_event_to_payload(event, key)
        headers = {
            "Content-Type": "application/json",
            "Bugsnag-Api-Key": key,
            "Bugsnag-Payload-Version": "5",
            "Bugsnag-Sent-At": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        }
        req = urllib.request.Request(_NOTIFY_URL, data=json.dumps(payload).encode("utf-8"),
                                     headers=headers, method="POST")
        urllib.request.urlopen(req, timeout=5).close()
        return True
    except Exception:  # noqa: BLE001 - reporting must never break the crash path
        return False


# --- Data Access API (read + resolve) --------------------------------------------------------

class BugsnagDataError(RuntimeError):
    """Raised by the triage client on a missing token/project or an API error."""


def _data_request(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    token = os.environ.get("BUGSNAG_AUTH_TOKEN", "").strip()
    if not token:
        raise BugsnagDataError("BUGSNAG_AUTH_TOKEN not set (Data Access personal token)")
    headers = {"Authorization": f"token {token}", "X-Version": "2",
               "Content-Type": "application/json"}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(_DATA_BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        detail = (e.read(400) or b"").decode("utf-8", "replace")
        raise BugsnagDataError(f"HTTP {e.code} {e.reason}: {detail}") from e


def _project_id() -> str:
    pid = os.environ.get("BUGSNAG_PROJECT_ID", "").strip()
    if not pid:
        raise BugsnagDataError("BUGSNAG_PROJECT_ID not set")
    return pid


def list_errors(status: str = "open", per_page: int = 30) -> list[dict]:
    """Errors in the project, newest-activity first. ``status``: open / fixed / ignored / """ \
        """snoozed (or '' for all)."""
    q = f"?per_page={per_page}&sort=last_seen&direction=desc"
    if status:
        q += f"&filters[error.status][][type]=eq&filters[error.status][][value]={status}"
    _, errors = _data_request("GET", f"/projects/{_project_id()}/errors{q}")
    return errors if isinstance(errors, list) else []


def error_detail(error_id: str) -> dict:
    _, err = _data_request("GET", f"/projects/{_project_id()}/errors/{error_id}")
    return err if isinstance(err, dict) else {}


def latest_event(error_id: str) -> dict:
    """Most recent full event for an error (carries the detailed stacktrace + context)."""
    _, ev = _data_request("GET", f"/projects/{_project_id()}/errors/{error_id}/latest_event")
    return ev if isinstance(ev, dict) else {}


def resolve_error(error_id: str, comment: str | None = None) -> bool:
    """Mark an error FIXED (closed). Optionally add a comment (e.g. the fixing commit/PR)."""
    pid = _project_id()
    _data_request("PATCH", f"/projects/{pid}/errors/{error_id}", {"operation": "fix"})
    if comment:
        try:
            _data_request("POST", f"/projects/{pid}/errors/{error_id}/comments",
                          {"message": comment})
        except BugsnagDataError:
            pass  # the fix landed; a failed comment is non-fatal
    return True


# --- triage CLI ------------------------------------------------------------------------------

def _frames_summary(event: dict, n: int = 8) -> str:
    try:
        st = event["exceptions"][0]["stacktrace"]
    except (KeyError, IndexError, TypeError):
        return "   (no stacktrace)"
    out = []
    for f in st[:n]:
        out.append(f"   {f.get('file')}:{f.get('lineNumber')} in {f.get('method')}"
                   + (f"\n       {f.get('code')}" if f.get('code') else ""))
    return "\n".join(out) or "   (empty)"


def main(argv: list[str] | None = None) -> int:
    import argparse

    try:  # convenience: pick up BUGSNAG_* from .env when run standalone (best-effort)
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:  # noqa: BLE001
        pass

    p = argparse.ArgumentParser(prog="python -m vike_trader_app.ai.bugsnag",
                                description="Read & resolve vike-trader Bugsnag errors.")
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("list", help="list errors")
    pl.add_argument("--status", default="open", help="open/fixed/ignored/snoozed, or '' for all")
    pl.add_argument("--limit", type=int, default=30)
    ps = sub.add_parser("show", help="show an error + latest stacktrace")
    ps.add_argument("error_id")
    pr = sub.add_parser("resolve", help="mark an error FIXED (close it)")
    pr.add_argument("error_id")
    pr.add_argument("--comment", default=None, help="comment to attach (e.g. commit/PR link)")
    args = p.parse_args(argv)

    try:
        if args.cmd == "list":
            errors = list_errors(status=args.status, per_page=args.limit)
            if not errors:
                print(f"No '{args.status or 'any'}' errors.")
                return 0
            for e in errors:
                print(f"{e.get('id')}  [{e.get('status')}]  x{e.get('events')}  "
                      f"{e.get('error_class')}: {str(e.get('message'))[:70]}")
            return 0
        if args.cmd == "show":
            err = error_detail(args.error_id)
            print(f"{err.get('error_class')}: {err.get('message')}")
            print(f"status={err.get('status')}  events={err.get('events')}  "
                  f"first={err.get('first_seen')}  last={err.get('last_seen')}")
            print("--- latest event stacktrace ---")
            print(_frames_summary(latest_event(args.error_id)))
            return 0
        if args.cmd == "resolve":
            resolve_error(args.error_id, comment=args.comment)
            print(f"resolved {args.error_id} (marked fixed)")
            return 0
    except BugsnagDataError as e:
        print(f"error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
