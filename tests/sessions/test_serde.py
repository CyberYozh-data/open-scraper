from __future__ import annotations

from src.sessions.serde import normalize_storage_state, storage_state_bytes


class TestNormalizeStorageState:
    def test_none_returns_none(self):
        assert normalize_storage_state(None) is None

    def test_empty_state_passes(self):
        assert normalize_storage_state({"cookies": [], "origins": []}) == {
            "cookies": [],
            "origins": [],
        }

    def test_expires_minus_one_is_dropped(self):
        # Playwright rejects null in expires on context creation; treat the
        # session-cookie sentinel by omitting the key entirely.
        state = {"cookies": [{"name": "s", "value": "1", "expires": -1}], "origins": []}
        out = normalize_storage_state(state)
        assert "expires" not in out["cookies"][0]

    def test_expires_non_finite_is_dropped(self):
        import math as _math
        state = {"cookies": [{"name": "s", "value": "1", "expires": _math.nan}], "origins": []}
        out = normalize_storage_state(state)
        assert "expires" not in out["cookies"][0]

    def test_expires_float_truncated_to_int(self):
        state = {"cookies": [{"name": "s", "value": "1", "expires": 1234567890.5}], "origins": []}
        out = normalize_storage_state(state)
        assert out["cookies"][0]["expires"] == 1234567890

    def test_expires_missing_passes(self):
        state = {"cookies": [{"name": "s", "value": "1"}], "origins": []}
        out = normalize_storage_state(state)
        assert "expires" not in out["cookies"][0] or out["cookies"][0]["expires"] is None

    def test_unknown_keys_preserved(self):
        state = {"cookies": [{"name": "s", "value": "1", "sameSite": "Lax"}], "origins": []}
        out = normalize_storage_state(state)
        assert out["cookies"][0]["sameSite"] == "Lax"


class TestStorageStateBytes:
    def test_none_is_zero(self):
        assert storage_state_bytes(None) == 0

    def test_empty_state(self):
        size = storage_state_bytes({"cookies": [], "origins": []})
        assert 0 < size < 100

    def test_large_state(self):
        big = {
            "cookies": [{"name": "s", "value": "x" * 100_000}],
            "origins": [],
        }
        assert storage_state_bytes(big) > 100_000
