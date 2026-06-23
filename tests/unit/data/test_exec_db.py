"""Exec ledger: append-only event audit, idempotent fill dedup, order snapshot upsert."""

from vike_trader_app.data import exec_db


def _db(tmp_path):
    return exec_db.connect_exec_db(tmp_path / "db" / "vike_trader_app.sqlite")


def test_append_event_then_read_back(tmp_path):
    conn = _db(tmp_path)
    exec_db.append_event(conn, ts=10, kind="OrderSubmitted", client_order_id="c1",
                         payload='{"x":1}')
    rows = conn.execute(
        "SELECT ts, kind, client_order_id, payload FROM exec_events ORDER BY id").fetchall()
    assert rows == [(10, "OrderSubmitted", "c1", '{"x":1}')]


def test_schema_is_idempotent(tmp_path):
    db = tmp_path / "db" / "vike_trader_app.sqlite"
    exec_db.connect_exec_db(db).close()
    conn = exec_db.connect_exec_db(db)  # second open must not raise
    assert conn.execute("SELECT count(*) FROM exec_events").fetchone()[0] == 0


def test_record_fill_dedups_on_trade_id(tmp_path):
    conn = _db(tmp_path)
    assert exec_db.record_fill(conn, trade_id="t1", client_order_id="c1", symbol="BTCUSDT",
                               side=1, qty=0.5, px=70000.0) is True
    # same trade_id arriving again on a reconnect must be ignored, not double-counted
    assert exec_db.record_fill(conn, trade_id="t1", client_order_id="c1", symbol="BTCUSDT",
                               side=1, qty=0.5, px=70000.0) is False
    assert conn.execute("SELECT count(*) FROM exec_fills").fetchone()[0] == 1


def test_upsert_order_snapshot_and_load(tmp_path):
    conn = _db(tmp_path)
    exec_db.upsert_order(conn, client_order_id="c1", venue="binance", symbol="BTCUSDT",
                         side=1, qty=1.0, order_type="limit", status="ACCEPTED", price=69000.0)
    exec_db.upsert_order(conn, client_order_id="c1", venue="binance", symbol="BTCUSDT",
                         side=1, qty=1.0, order_type="limit", status="FILLED", price=69000.0,
                         filled_qty=1.0, avg_fill_px=69010.0)
    orders = exec_db.load_orders(conn)
    assert len(orders) == 1
    assert orders[0]["status"] == "FILLED" and orders[0]["filled_qty"] == 1.0
