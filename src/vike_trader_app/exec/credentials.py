"""Per-venue API credentials loaded from the gitignored .env (no keyring; .env is the store).

`load_credentials` returns None when the key/secret env vars are unset or blank — that absence
IS the live gate (creds absent -> stay paper). `env_var_names` is the SINGLE place env vars are
named, so adding OKX/Bybit later is one mapping entry. Piggybacks the GUI's load_dotenv().
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class Environment(Enum):
    SIM = "SIM"
    DEMO = "DEMO"
    MAINNET = "MAINNET"


@dataclass(frozen=True)
class Credentials:
    api_key: str
    api_secret: str
    passphrase: str | None = None

    def __repr__(self) -> str:
        tail = self.api_key[-4:] if self.api_key else ""
        return f"Credentials(api_key=***{tail}, passphrase={'set' if self.passphrase else 'None'})"

    __str__ = __repr__


def env_var_names(venue: str, env: Environment) -> tuple[str, str, str]:
    """The (key, secret, passphrase) env-var names for a venue/environment. The ONE naming site."""
    prefix = f"{venue.upper()}_{env.value}"
    return (f"{prefix}_API_KEY", f"{prefix}_API_SECRET", f"{prefix}_PASSPHRASE")


def load_credentials(venue: str, env: Environment) -> Credentials | None:
    """Read creds from os.environ; None when key or secret is unset/blank (the live gate)."""
    key_name, secret_name, passphrase_name = env_var_names(venue, env)
    api_key = (os.environ.get(key_name) or "").strip()
    api_secret = (os.environ.get(secret_name) or "").strip()
    if not api_key or not api_secret:
        return None
    passphrase = (os.environ.get(passphrase_name) or "").strip() or None
    return Credentials(api_key=api_key, api_secret=api_secret, passphrase=passphrase)
