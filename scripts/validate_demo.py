"""Smoke-run the anti-overfit report on real/seeded data and print the verdict.

Run:  uv run python scripts/validate_demo.py
"""

from vike_trader_app.analysis.report import build_overfit_report
from vike_trader_app.ui.dialogs import SmaCross

SEED = "storage/parquet/BTCUSDT/1m.parquet"


def _bars():
    try:
        from vike_trader_app.data.parquet_source import read_bars_parquet

        bars = read_bars_parquet(SEED)
        if bars:
            print(f"loaded {len(bars)} bars from {SEED}")
            return bars
    except Exception as exc:  # noqa: BLE001
        print(f"parquet load skipped: {exc}")
    from vike_trader_app.data.binance_source import fetch_bars

    bars = fetch_bars("BTCUSDT", "1m", 1000)
    print(f"fetched {len(bars)} bars from Binance")
    return bars


def main():
    report = build_overfit_report(
        _bars(), SmaCross.make, SmaCross.PARAM_GRID, n_splits=4, fee_rate=0.001
    )
    print(f"\n=== Anti-overfit report ({report.n_trials} configurations) ===")
    print(f"best params      : {report.best_params}")
    print(f"best Sharpe (ann): {report.best_sharpe:.2f}")
    print(f"deflated Sharpe  : {report.deflated_sharpe:.1%}")
    print(f"PBO              : {report.pbo:.1%}")
    print(f"\n>>> Overfit risk: {report.verdict.level}")
    for reason in report.verdict.reasons:
        print(f"    - {reason}")


if __name__ == "__main__":
    main()
