from vike_trader_app.exec.arm_spec import ExecArmSpec, resolve_arm_spec


def test_normalizes_casing_and_defaults_spot():
    s = resolve_arm_spec(venue="Binance", environment="demo", product=None,
                         symbol="BTCUSDT", leverage=None, env={})
    assert s == ExecArmSpec("binance", "DEMO", "spot", "BTCUSDT", 1.0)


def test_explicit_perp_and_leverage():
    s = resolve_arm_spec(venue="bybit", environment="DEMO", product="perp",
                         symbol="BTCUSDT", leverage=5, env={})
    assert s.product == "perp" and s.leverage == 5.0


def test_env_fallback_when_field_is_none():
    env = {"VIKE_EXEC_VENUE": "okx", "VIKE_EXEC_ENV": "MAINNET",
           "VIKE_EXEC_PRODUCT": "perp", "VIKE_EXEC_LEVERAGE": "3"}
    s = resolve_arm_spec(venue=None, environment=None, product=None,
                         symbol="BTCUSDT", leverage=None, env=env)
    assert s == ExecArmSpec("okx", "MAINNET", "perp", "BTCUSDT", 3.0)


def test_returns_none_on_missing_venue_or_env():
    assert resolve_arm_spec(venue=None, environment="DEMO", product="spot",
                            symbol="BTCUSDT", leverage=None, env={}) is None
    assert resolve_arm_spec(venue="binance", environment=None, product="spot",
                            symbol="BTCUSDT", leverage=None, env={}) is None


def test_leverage_floored_at_one_and_spot_forces_one():
    s = resolve_arm_spec(venue="binance", environment="DEMO", product="spot",
                         symbol="BTCUSDT", leverage=10, env={})
    assert s.leverage == 1.0   # spot never levered
    s2 = resolve_arm_spec(venue="binance", environment="DEMO", product="perp",
                          symbol="BTCUSDT", leverage=0.0, env={})
    assert s2.leverage == 1.0  # floored
