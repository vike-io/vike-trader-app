"""Unit tests for the Bugsnag integration (crash forwarding + Data Access triage), no network."""

import json

import pytest

from vike_trader_app.ai import bugsnag

_TB = (
    "Traceback (most recent call last):\n"
    '  File "vike_trader_app/ui/app.py", line 100, in _load_symbol\n'
    "    self.do()\n"
    '  File "vike_trader_app/core/engine.py", line 42, in do\n'
    "    raise ValueError(\"bad symbol\")\n"
    "ValueError: bad symbol\n"
)


def _event(**over):
    e = {"kind": "python_main", "exc_type": "ValueError", "traceback": _TB,
         "app_version": "1.2.3", "ts_ms": 111, "client": "cid-abc",
         "env": {"python": "3.14.0", "qt": "6.9", "platform": "win32", "os": "Windows-11"}}
    e.update(over)
    return e


# --- parse_python_traceback ------------------------------------------------------------------

def test_parse_traceback_innermost_first():
    frames = bugsnag.parse_python_traceback(_TB)
    assert len(frames) == 2
    assert frames[0]["file"] == "vike_trader_app/core/engine.py"   # innermost (crash site) first
    assert frames[0]["lineNumber"] == 42 and frames[0]["method"] == "do"
    assert frames[1]["file"] == "vike_trader_app/ui/app.py" and frames[1]["lineNumber"] == 100
    assert all(f["inProject"] for f in frames)


def test_parse_traceback_empty_when_no_frames():
    assert bugsnag.parse_python_traceback("Windows fatal exception: access violation\n<no c>") == []


# --- crash_event_to_payload ------------------------------------------------------------------

def test_payload_python_crash():
    p = bugsnag.crash_event_to_payload(_event(), "KEY123")
    assert p["apiKey"] == "KEY123"
    ev = p["events"][0]
    exc = ev["exceptions"][0]
    assert exc["errorClass"] == "ValueError"
    assert exc["message"] == "ValueError: bad symbol"
    assert exc["stacktrace"][0]["method"] == "do"           # innermost first
    assert ev["unhandled"] is True
    assert ev["app"] == {"version": "1.2.3", "releaseStage": "production", "type": "desktop"}
    assert ev["device"]["osName"] == "win32"
    assert ev["device"]["runtimeVersions"] == {"python": "3.14.0", "qt": "6.9"}
    assert ev["user"]["id"] == "cid-abc"
    assert ev["metaData"]["crash"]["kind"] == "python_main"


def test_payload_native_crash_gets_synthetic_frame():
    e = _event(kind="native", exc_type=None,
               traceback="Windows fatal exception: access violation\n<cannot get C stack>")
    p = bugsnag.crash_event_to_payload(e, "K")
    exc = p["events"][0]["exceptions"][0]
    assert exc["errorClass"] == "NativeCrash"               # mapped from kind
    assert len(exc["stacktrace"]) == 1                      # synthetic frame, never empty
    assert exc["stacktrace"][0]["method"] == "native"


def test_payload_release_stage_env(monkeypatch):
    monkeypatch.setenv("VIKE_RELEASE_STAGE", "development")
    p = bugsnag.crash_event_to_payload(_event(), "K")
    assert p["events"][0]["app"]["releaseStage"] == "development"


# --- report_crash (forwarding) ---------------------------------------------------------------

class _FakeResp:
    def __init__(self, status=200, body=b"OK"):
        self.status, self._b = status, body

    def read(self, n=None):
        return self._b

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_report_crash_noop_without_key(monkeypatch):
    monkeypatch.delenv("BUGSNAG_API_KEY", raising=False)
    assert bugsnag.report_crash(_event()) is False


def test_report_crash_posts_with_headers(monkeypatch):
    monkeypatch.setenv("BUGSNAG_API_KEY", "NOTIFYKEY")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp()

    monkeypatch.setattr(bugsnag.urllib.request, "urlopen", fake_urlopen)
    assert bugsnag.report_crash(_event()) is True
    assert captured["url"].startswith("https://notify.bugsnag.com")
    assert captured["headers"]["bugsnag-api-key"] == "NOTIFYKEY"
    assert captured["headers"]["bugsnag-payload-version"] == "5"
    assert captured["body"]["apiKey"] == "NOTIFYKEY"


def test_report_crash_swallows_errors(monkeypatch):
    monkeypatch.setenv("BUGSNAG_API_KEY", "K")

    def boom(req, timeout=None):
        raise OSError("network down")

    monkeypatch.setattr(bugsnag.urllib.request, "urlopen", boom)
    assert bugsnag.report_crash(_event()) is False          # never raises


# --- Data Access API (read + resolve) --------------------------------------------------------

def test_data_request_requires_token(monkeypatch):
    monkeypatch.delenv("BUGSNAG_AUTH_TOKEN", raising=False)
    with pytest.raises(bugsnag.BugsnagDataError):
        bugsnag.list_errors()


def _wire_data(monkeypatch, capture, response):
    monkeypatch.setenv("BUGSNAG_AUTH_TOKEN", "TOK")
    monkeypatch.setenv("BUGSNAG_PROJECT_ID", "PROJ")

    def fake_urlopen(req, timeout=None):
        capture["method"] = req.get_method()
        capture["url"] = req.full_url
        capture["headers"] = {k.lower(): v for k, v in req.header_items()}
        capture["body"] = json.loads(req.data.decode()) if req.data else None
        return _FakeResp(200, json.dumps(response).encode())

    monkeypatch.setattr(bugsnag.urllib.request, "urlopen", fake_urlopen)


def test_list_errors_builds_authed_get(monkeypatch):
    cap = {}
    _wire_data(monkeypatch, cap, [{"id": "e1", "error_class": "X"}])
    out = bugsnag.list_errors(status="open", per_page=5)
    assert out[0]["id"] == "e1"
    assert cap["method"] == "GET"
    assert "/projects/PROJ/errors" in cap["url"] and "per_page=5" in cap["url"]
    assert cap["headers"]["authorization"] == "token TOK"
    assert cap["headers"]["x-version"] == "2"


def test_resolve_error_patches_fix(monkeypatch):
    cap = {}
    _wire_data(monkeypatch, cap, {})
    assert bugsnag.resolve_error("err42") is True
    assert cap["method"] == "PATCH"
    assert cap["url"].endswith("/projects/PROJ/errors/err42")
    assert cap["body"] == {"operation": "fix"}
