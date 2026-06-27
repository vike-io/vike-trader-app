"""Deribit (JSON-RPC 2.0 over WebSocket + OAuth2) execution adapter — Phase 6.

NOT a CryptoExecutionClient subclass: Deribit's v2 API is JSON-RPC over one authed WS, not REST/HMAC.
The reuse boundary is the LiveOmsHub duck-typed seam (submit/cancel/connect) + the fill-mapper
contract, NOT the REST base. 6a is the offline foundation (no socket, no creds).
"""
