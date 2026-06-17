"""Sandbox child entry: read a JSON job on stdin, run the strategy, write a JSON result on stdout.

Spawned by ``core.sandbox.run_sandboxed`` in a separate process so an AI-generated strategy that
hangs/leaks/misbehaves can be hard-killed via a wall-clock timeout. Always prints valid JSON last.
"""

import json
import sys


def main() -> None:
    try:
        job = json.loads(sys.stdin.read())
        from vike_trader_app.core.model import Bar
        from vike_trader_app.core.strategy_loader import load_strategy_from_string
        from vike_trader_app.tester import StrategyTester, TesterConfig

        bars = [Bar(ts=b[0], open=b[1], high=b[2], low=b[3], close=b[4], volume=b[5], funding=b[6])
                for b in job["bars"]]
        config = TesterConfig(**job["config"])
        # Drop process-creation / raw-networking / ptrace (Linux, best-effort) BEFORE compiling or
        # running the untrusted strategy source — see harden.py. No-op off Linux / without libseccomp.
        from .harden import apply_child_hardening
        apply_child_hardening()
        cls = load_strategy_from_string(job["code"], validate=True)
        report = StrategyTester(cls(), bars, config).run()
        print(json.dumps({"ok": True, "report": report.as_dict()}, default=str))
    except Exception as exc:  # noqa: BLE001 - any failure becomes a JSON error result, never a crash
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))


if __name__ == "__main__":
    main()
