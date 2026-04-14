from __future__ import annotations

from src.utils.ids import new_request_id


def test_new_request_id_default_prefix():
    """Check default prefix"""
    request_id = new_request_id()
    assert request_id.startswith("req_")


def test_new_request_id_custom_prefix():
    """Check custom prefix"""
    request_id = new_request_id(prefix="job")
    assert request_id.startswith("job_")


def test_new_request_id_unique():
    """Check unique ID"""
    ids = [new_request_id() for _ in range(100)]
    assert len(ids) == len(set(ids)), "ID must be unique"


def test_new_request_id_format():
    """Check ID format"""
    request_id = new_request_id(prefix="test")
    parts = request_id.split("_")

    assert len(parts) == 2, "ID must be from prefix_hex"
    assert parts[0] == "test", "First part must be prefix"
    assert len(parts[1]) == 24, "Hex part must be from 24 symbols (12 bytes)"
    assert all(c in "0123456789abcdef" for c in parts[1]), "Hex part must have only hex symbols"
