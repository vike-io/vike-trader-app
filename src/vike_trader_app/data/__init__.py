"""data — the data-access layer (resolution-agnostic) and metadata store.

Modules (created during implementation, test-first):
    source.py            DataSource interface -> one uniform bar/tick stream
    parquet_source.py    local historical bars via Polars (reads ./storage/parquet/)
    binance_source.py    seed test data from Binance public REST klines (no API key)
    websocket_source.py  vike.io live feed for forward-test (Phase 3)
    store.py             SQLite metadata (strategies, runs, results) in ./storage/db/

NOTE: this is the data *code* layer. Actual data files live under ./storage/
(git-ignored by extension), never inside this package.
"""
