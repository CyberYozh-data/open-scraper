from __future__ import annotations

from pydantic import BaseModel


class ProxyConfig(BaseModel):
    server: str  # e.g. socks5://host:port or http://host:port
    username: str | None = None
    password: str | None = None
    # Resolved premium targeting tokens (country/session/filter/…), without the
    # account login. Set only by the v2 premium provider; safe to echo to clients
    # (carries no credential). None for every other proxy type.
    targeting_suffix: str | None = None
