from __future__ import annotations

from pydantic import BaseModel


class ProxyConfig(BaseModel):
    server: str  # e.g. socks5://host:port or http://host:port
    username: str | None = None
    password: str | None = None
