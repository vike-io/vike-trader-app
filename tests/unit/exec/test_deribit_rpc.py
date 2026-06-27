"""JSON-RPC 2.0 request builder (monotonic id) + response parse (result XOR error)."""
from vike_trader_app.exec.deribit.rpc import JsonRpcBuilder, parse_response


def test_request_builds_jsonrpc_frame_with_monotonic_id():
    b = JsonRpcBuilder(start=10)
    f1 = b.request("private/buy", {"instrument_name": "BTC-PERPETUAL"})
    f2 = b.request("private/sell", {"instrument_name": "BTC-PERPETUAL"})
    assert f1 == {"jsonrpc": "2.0", "id": 10, "method": "private/buy",
                  "params": {"instrument_name": "BTC-PERPETUAL"}}
    assert f2["id"] == 11
    assert f2["method"] == "private/sell"


def test_next_id_is_monotonic_and_independent_of_request():
    b = JsonRpcBuilder()
    assert b.next_id() == 1
    assert b.next_id() == 2
    assert b.request("public/test", {})["id"] == 3


def test_parse_response_result():
    rid, result, error = parse_response(
        {"jsonrpc": "2.0", "id": 5, "result": {"order": {"order_id": "ETH-1"}}})
    assert rid == 5
    assert result == {"order": {"order_id": "ETH-1"}}
    assert error is None


def test_parse_response_error():
    rid, result, error = parse_response(
        {"jsonrpc": "2.0", "id": 6, "error": {"code": 11044, "message": "not_open_order"}})
    assert rid == 6
    assert result is None
    assert error == {"code": 11044, "message": "not_open_order"}


def test_parse_response_tolerates_missing_id_and_non_dict():
    assert parse_response({"result": {}}) == (None, {}, None)
    assert parse_response("pong") == (None, None, None)
    assert parse_response({}) == (None, None, None)
