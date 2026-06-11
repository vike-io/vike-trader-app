"""Tests for ai.telemetry — opt-in MCP usage telemetry (privacy + schema preservation).

Client state lives in the app SQLite DB (``telemetry_meta`` / ``telemetry_usage``), not files;
every test points BOTH seams (``VIKE_TELEMETRY_DB`` + legacy ``VIKE_TELEMETRY_DIR``) at tmp_path
so a developer's real app DB / legacy store is never read, written, or swept by the suite.
"""

import json
import sqlite3
from contextlib import closing

import pytest

from vike_trader_app.ai import mcp_server, telemetry


def _isolate(monkeypatch, tmp_path):
    """Point telemetry at a per-test DB + legacy dir; returns (db_path, legacy_dir)."""
    db = tmp_path / "app.sqlite"
    legacy = tmp_path / "telemetry"
    monkeypatch.setenv("VIKE_TELEMETRY_DB", str(db))
    monkeypatch.setenv("VIKE_TELEMETRY_DIR", str(legacy))
    return db, legacy


def _events(db):
    """All telemetry_usage events (insert order), decoded."""
    with closing(sqlite3.connect(db)) as conn:
        rows = conn.execute("SELECT event FROM telemetry_usage ORDER BY id").fetchall()
    return [json.loads(r[0]) for r in rows]


def _meta(db, key):
    with closing(sqlite3.connect(db)) as conn:
        row = conn.execute("SELECT value FROM telemetry_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def test_noop_when_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("VIKE_TELEMETRY", raising=False)
    db, _ = _isolate(monkeypatch, tmp_path)
    assert telemetry.instrument(lambda x: x * 2)(3) == 6
    assert not db.exists()  # disabled => not even the DB file is created


def test_records_safe_event_never_leaks_source(tmp_path, monkeypatch):
    monkeypatch.setenv("VIKE_TELEMETRY", "1")
    db, _ = _isolate(monkeypatch, tmp_path)

    @telemetry.instrument
    def fake(strategy_code: str, symbol: str, config: dict | None = None):
        return {"ran": True}

    fake(strategy_code="class StrategyABC: pass", symbol="AAPL", config={"cash": 5000})
    ev = _events(db)[-1]

    assert ev["tool"] == "fake" and ev["ok"] is True and "duration_ms" in ev
    assert ev["args"]["symbol"] == "AAPL"
    # source replaced by a sha + length; config reduced to its keys
    assert "strategy_code_sha" in ev["args"] and ev["args"]["strategy_code_len"] == 23
    assert ev["args"]["config"] == {"_keys": ["cash"]}
    assert "StrategyABC" not in json.dumps(ev)


def test_records_error_and_reraises(tmp_path, monkeypatch):
    monkeypatch.setenv("VIKE_TELEMETRY", "1")
    db, _ = _isolate(monkeypatch, tmp_path)

    @telemetry.instrument
    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        boom()
    ev = _events(db)[-1]
    assert ev["ok"] is False and ev["error"] == "ValueError"


def test_client_id_lazy_created_in_db_and_stable(tmp_path, monkeypatch):
    db, _ = _isolate(monkeypatch, tmp_path)
    cid = telemetry._client_id()
    assert len(cid) == 32 and all(c in "0123456789abcdef" for c in cid)
    assert telemetry._client_id() == cid  # stable across calls
    assert _meta(db, "client_id") == cid  # persisted in telemetry_meta, not a file


def test_migrates_legacy_files_into_db_and_deletes_them(tmp_path, monkeypatch):
    db, legacy = _isolate(monkeypatch, tmp_path)
    legacy.mkdir()
    seeded_cid = "ab" * 16
    (legacy / "client_id").write_text(seeded_cid, encoding="utf-8")
    ev1 = {"ts_ms": 1000, "client": seeded_cid, "tool": "run_backtest", "ok": True}
    ev2 = {"ts_ms": 2000, "client": seeded_cid, "tool": "run_scanner", "ok": False}
    (legacy / "mcp-usage.jsonl").write_text(
        json.dumps(ev1) + "\n{not json\n" + json.dumps(ev2) + "\n", encoding="utf-8"
    )

    assert telemetry._client_id() == seeded_cid  # first DB touch sweeps the legacy store

    assert _meta(db, "client_id") == seeded_cid
    assert _events(db) == [ev1, ev2]  # corrupt line skipped, order kept
    assert not (legacy / "client_id").exists()
    assert not (legacy / "mcp-usage.jsonl").exists()
    assert not legacy.exists()  # the emptied legacy dir is removed too


def test_record_inserts_row_with_ts_seconds(tmp_path, monkeypatch):
    db, _ = _isolate(monkeypatch, tmp_path)
    telemetry.record({"ts_ms": 1234500, "client": "c", "tool": "x", "ok": True})
    with closing(sqlite3.connect(db)) as conn:
        ts, event = conn.execute("SELECT ts, event FROM telemetry_usage").fetchone()
    assert ts == pytest.approx(1234.5)  # ts_ms / 1000 -> REAL epoch seconds
    assert json.loads(event)["tool"] == "x"


def _capture_post_headers(monkeypatch):
    """Run telemetry._post with the network stubbed out; return the headers it built."""
    import urllib.request

    captured = {}

    class _FakeReq:
        def __init__(self, url, data=None, headers=None, method=None):
            captured["headers"] = dict(headers or {})

    class _FakeResp:
        def close(self):
            pass

    monkeypatch.setattr(urllib.request, "Request", _FakeReq)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: _FakeResp())
    telemetry._post({"ts_ms": 1, "client": "t", "tool": "x"}, "http://example.invalid/telemetry")
    return captured.get("headers", {})


def test_post_attaches_token_header_when_set(monkeypatch):
    monkeypatch.setenv("VIKE_TELEMETRY_TOKEN", "s3cr3t")
    headers = _capture_post_headers(monkeypatch)
    assert headers.get("x-vike-token") == "s3cr3t"
    assert headers.get("Content-Type") == "application/json"


def test_post_omits_token_header_when_unset(monkeypatch):
    monkeypatch.delenv("VIKE_TELEMETRY_TOKEN", raising=False)
    headers = _capture_post_headers(monkeypatch)
    assert "x-vike-token" not in headers


def test_report_crash_noop_when_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("VIKE_TELEMETRY", raising=False)
    monkeypatch.delenv("VIKE_CRASH_REPORTS", raising=False)
    _isolate(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(telemetry, "record", lambda ev: calls.append(ev))

    telemetry.report_crash({"kind": "python_main", "exc_type": "E", "traceback": "t"})

    assert calls == []  # nothing recorded or uploaded when opt-in is off


def test_report_crash_records_scrubbed_event_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("VIKE_TELEMETRY", "1")
    _isolate(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(telemetry, "record", lambda ev: calls.append(ev))

    telemetry.report_crash({"kind": "native", "exc_type": "Segfault", "traceback": "boom"})

    assert len(calls) == 1
    ev = calls[0]
    assert ev["type"] == "crash" and ev["kind"] == "native"
    assert "client" in ev and "ts_ms" in ev
    assert "python" in ev["env"]  # safe environment block present


def test_report_crash_enables_independently_via_crash_reports_env(tmp_path, monkeypatch):
    monkeypatch.delenv("VIKE_TELEMETRY", raising=False)  # usage telemetry OFF
    monkeypatch.setenv("VIKE_CRASH_REPORTS", "1")  # crash reporting ON on its own
    _isolate(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(telemetry, "record", lambda ev: calls.append(ev))

    telemetry.report_crash({"kind": "python_main"})

    assert len(calls) == 1


def test_report_crash_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("VIKE_CRASH_REPORTS", "1")
    _isolate(monkeypatch, tmp_path)

    def boom(ev):
        raise RuntimeError("network down")

    monkeypatch.setattr(telemetry, "record", boom)
    telemetry.report_crash({"kind": "python_main"})  # must not propagate


def test_build_server_preserves_tool_schema_through_wrapper():
    srv = mcp_server.build_server()
    names = mcp_server.tool_names(srv)
    assert {"run_backtest", "run_scanner", "run_portfolio_backtest"} <= set(names)
    tools = {t.name: t for t in srv._tool_manager.list_tools()}
    props = tools["run_backtest"].parameters["properties"]
    assert {"strategy_code", "symbol", "interval"} <= set(props)
