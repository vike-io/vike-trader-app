# vike-trader-app

A Python-native, crypto-first backtesting & forward-testing platform with a visual
desktop UI (PySide6) and a headless engine. Backtest / forward-test only — no live execution.

## Install

```bash
uv sync --all-extras    # or: pip install -e ".[ui,analysis,fast,opt,viz]"
```

## Run

```bash
uv run vike-trader-app-gui    # visual backtester (desktop)
uv run pytest                 # test suite
```

## License

MIT — see [LICENSE](LICENSE).
