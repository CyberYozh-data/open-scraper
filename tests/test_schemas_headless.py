from src.schemas import ScrapeRequest


def _req(**kw):
    return ScrapeRequest(url="https://example.com", **kw)


def test_headless_defaults_to_none():
    """Unset means 'use the server default' — not True/False."""
    assert _req().headless is None


def test_headless_accepts_true_and_false():
    assert _req(headless=True).headless is True
    assert _req(headless=False).headless is False
