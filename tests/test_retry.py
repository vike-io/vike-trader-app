"""Provider-agnostic exponential backoff (generalised from the Dukascopy retry).

``with_backoff`` runs a call and retries transient failures with ``base * 2**attempt`` delays,
so heavy historical pulls survive a 429/5xx blip across any provider (only Dukascopy had retry
before). The clock is injected, so tests run without real delay.
"""

import pytest

from vike_trader_app.data.retry import with_backoff


def test_returns_on_first_success():
    calls = []
    out = with_backoff(lambda: (calls.append(1), "ok")[1], sleep=lambda _s: None)
    assert out == "ok" and len(calls) == 1


def test_retries_then_succeeds_with_exponential_delays():
    n = {"i": 0}
    sleeps: list[float] = []

    def call():
        n["i"] += 1
        if n["i"] < 3:
            raise ValueError("transient")
        return "ok"

    assert with_backoff(call, sleep=sleeps.append, base=0.5) == "ok"
    assert n["i"] == 3
    assert sleeps == [0.5, 1.0]  # base*2^0, base*2^1 (no sleep after the success)


def test_raises_after_exhausting_tries():
    n = {"i": 0}

    def call():
        n["i"] += 1
        raise ValueError("always")

    with pytest.raises(ValueError):
        with_backoff(call, tries=3, sleep=lambda _s: None)
    assert n["i"] == 3


def test_non_transient_is_not_retried():
    n = {"i": 0}

    def call():
        n["i"] += 1
        raise ValueError("permanent")

    with pytest.raises(ValueError):
        with_backoff(call, sleep=lambda _s: None, is_transient=lambda _e: False)
    assert n["i"] == 1  # gave up immediately


def test_only_retries_listed_exception_types():
    with pytest.raises(KeyError):
        with_backoff(lambda: (_ for _ in ()).throw(KeyError("x")),
                     retry_on=(ValueError,), sleep=lambda _s: None)
