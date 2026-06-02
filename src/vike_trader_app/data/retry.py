"""Provider-agnostic exponential backoff for transient network failures.

Generalises the per-hour retry that lived only in ``dukascopy_source`` so any provider's fetch
can survive a 429/5xx/connection blip during a heavy historical pull. The clock is injected so
tests run without real delay.
"""

import time


def with_backoff(call, *, tries: int = 4, sleep=time.sleep, base: float = 0.5,
                 retry_on: tuple = (Exception,), is_transient=None):
    """Run ``call()``, retrying failures with ``base * 2**attempt`` backoff.

    Retries exceptions in ``retry_on`` (default: any) for which ``is_transient(err)`` is truthy
    (default: always). The final attempt's error propagates; ``sleep``/``base`` are injectable.
    """
    last_attempt = tries - 1
    for attempt in range(tries):
        try:
            return call()
        except retry_on as err:
            if attempt == last_attempt or (is_transient is not None and not is_transient(err)):
                raise
            sleep(base * (2 ** attempt))
    raise RuntimeError("with_backoff: tries must be >= 1")  # unreachable for tries >= 1
