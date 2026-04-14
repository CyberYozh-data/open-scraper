from __future__ import annotations
import secrets


def new_request_id(prefix: str = "req") -> str:
    return f"{prefix}_{secrets.token_hex(12)}"
