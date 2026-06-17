# Headless Linux image: the vike-trader MCP server + backtest engine. NO GUI (no PySide6/Qt).
#
#   docker build -t vike-trader-mcp .
#   docker run --rm -i -v vike-data:/app/storage vike-trader-mcp        # stdio MCP server
#
# The server speaks JSON-RPC over stdio, so it isn't a long-running daemon — an MCP client attaches
# to it, e.g. in claude_desktop_config.json:
#   "vike-docker": { "command": "docker",
#     "args": ["run","--rm","-i","-v","vike-data:/app/storage","vike-trader-mcp"] }
# Remote (on prod1) just wraps that in ssh:
#   "vike-linux": { "command": "ssh", "args": ["prod1","docker run --rm -i -v vike-data:/app/storage vike-trader-mcp"] }
FROM python:3.12-slim

# libseccomp2 : runtime for the in-child seccomp denylist (the `sandbox` extra / pyseccomp).
# ca-certificates: so fetch_ohlcv can reach Binance/Yahoo over HTTPS.
# libgomp1   : OpenMP runtime numba's threading layer links (degrades without it, but cheap to add).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libseccomp2 ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency layer — cached unless the packaging or sources change. Headless extras only:
# mcp (server), fast (numba sweep), sandbox (pyseccomp confinement). NOT `ui` (no Qt in a server).
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir ".[mcp,fast,sandbox]"

# Cached Parquet lives on a mounted volume; fetch_ohlcv writes it, run_backtest reads it.
ENV VIKE_DATA_ROOT=/app/storage/parquet
RUN mkdir -p /app/storage/parquet

# Run as non-root — defence-in-depth, since the server executes AI-generated strategy code (the
# in-app sandbox confines it further; the container itself is the outer boundary).
RUN useradd --create-home --uid 10001 vike && chown -R vike:vike /app/storage
USER vike
VOLUME ["/app/storage"]

# stdio JSON-RPC MCP server; clients attach via `docker run -i` / `docker exec -i`.
ENTRYPOINT ["vike-mcp"]
