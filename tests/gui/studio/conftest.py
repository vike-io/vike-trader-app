"""Studio GUI test isolation: force the AI assistant OFFLINE for every Studio test.

``StudioTab.__init__`` -> ``_load_ai_settings`` -> ``_rebuild_agent_client`` builds a REAL LLM client
from whatever the *dev machine* has configured — a Cerebras key persisted in QSettings
(``ai/cerebras_key``, saved the first time the assistant was used) or ``ANTHROPIC_API_KEY`` in the
environment. A Studio test that then emits a chat prompt WITHOUT setting its own client (e.g.
``test_chat_without_client_is_graceful``, ``test_prompt_without_client_is_graceful_no_worker``) would
therefore take the client-present branch in ``_on_prompt`` and spawn a REAL ``ChatWorker`` QThread
that calls the live LLM over the network. That both (a) hits the network in a headless run and (b)
hard-crashes the worker process at Python-3.14 Qt teardown (``0xC0000409``) when the real thread + GC
unwind — the crash reproduced ONLY on configured dev machines (CI has no saved key, so the client is
None there and the suite is green). The persisted key is real-app state leaking into tests, exactly
the failure mode the top-level conftest already guards for sessions/live-feeds (VIKE_DISABLE_SESSION /
VIKE_DISABLE_LIVE).

Fix: neutralise the client build so ``_agent_client`` stays None regardless of QSettings/env. Tests
that exercise the AI path set a fake client explicitly via ``set_agent_client()`` AFTER construction,
which this does not touch.
"""
import sys

import pytest


@pytest.fixture(autouse=True)
def _no_ai_client_from_local_state(monkeypatch):
    studio = sys.modules.get("vike_trader_app.ui.studio")
    if studio is None:  # the GUI run always has it; be defensive if collection order changes
        import vike_trader_app.ui.studio as studio
    monkeypatch.setattr(
        studio.StudioTab, "_rebuild_agent_client",
        lambda self, **kw: setattr(self, "_agent_client", None),
    )
    yield
