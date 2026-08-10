import pytest
from pydantic import ValidationError
from src.schemas import ScrapeRequest


def test_browser_engine_defaults_to_chromium():
    req = ScrapeRequest(url="https://example.com")
    assert req.browser_engine == "chromium"
    assert req.humanize is False
    assert req.block_webgl is False
    assert req.spoof_os is None
    assert req.addons is None


def test_browser_engine_accepts_camoufox():
    req = ScrapeRequest(url="https://example.com", browser_engine="camoufox")
    assert req.browser_engine == "camoufox"


def test_invalid_engine_rejected():
    with pytest.raises(ValidationError):
        ScrapeRequest(url="https://example.com", browser_engine="links2")


def test_mobile_with_firefox_rejected():
    with pytest.raises(ValidationError, match="mobile.*not supported"):
        ScrapeRequest(url="https://example.com", browser_engine="firefox", device="mobile")


def test_mobile_with_camoufox_rejected():
    with pytest.raises(ValidationError, match="mobile.*not supported"):
        ScrapeRequest(url="https://example.com", browser_engine="camoufox", device="mobile")


def test_mobile_with_chromium_allowed():
    req = ScrapeRequest(url="https://example.com", browser_engine="chromium", device="mobile")
    assert req.device == "mobile"


class TestCamoufoxAcceptedAndIgnored:
    """Camoufox documents cookies / storage_state / render as accepted-and-ignored.

    That is worse than unsupported: the request succeeds, returns HTTP 200 and
    real HTML, and the caller has no way to learn that the thing they asked for
    did not happen. A session-pinned scrape is the sharp case — the session's
    storage_state never reaches the browser, so the page comes back logged OUT
    with no error anywhere. Reject the combination until the runner can honour
    it; `device='mobile'` on firefox/camoufox already sets that precedent.
    """

    @pytest.mark.parametrize(
        "extra, needle",
        [
            ({"session_id": "sess_abc"}, "session_id"),
            ({"cookies": [{"name": "a", "value": "b", "domain": "e.com", "path": "/"}]},
             "cookies"),
            ({"render": False}, "render"),
        ],
    )
    def test_camoufox_refuses_what_it_would_silently_drop(self, extra, needle):
        with pytest.raises(ValidationError) as exc:
            ScrapeRequest(url="https://e.com", browser_engine="camoufox", **extra)
        assert needle in str(exc.value)
        assert "camoufox" in str(exc.value)

    def test_camoufox_without_them_is_fine(self):
        req = ScrapeRequest(url="https://e.com", browser_engine="camoufox")
        assert req.browser_engine == "camoufox"

    @pytest.mark.parametrize("extra", [
        {"session_id": "sess_abc"},
        {"cookies": [{"name": "a", "value": "b", "domain": "e.com", "path": "/"}]},
        {"render": False},
    ])
    def test_the_engines_that_honour_them_are_untouched(self, extra):
        req = ScrapeRequest(url="https://e.com", browser_engine="chromium", **extra)
        assert req.browser_engine == "chromium"


def test_fingerprint_profile_defaults_to_unset():
    """Additive: a request that never heard of profiles is unchanged."""
    req = ScrapeRequest(url="https://example.com")

    assert req.fingerprint_profile is None


def test_a_profile_and_a_matching_spoof_os_are_accepted():
    req = ScrapeRequest(
        url="https://example.com", browser_engine="camoufox",
        spoof_os="windows", fingerprint_profile="windows",
    )

    assert req.fingerprint_profile == "windows"


def test_a_profile_contradicting_spoof_os_is_refused():
    """Two channels for one decision.

    Preferring one silently is how a caller ends up with an OS they did not ask
    for — the same reason the preset materializer has a session_id conflict
    error rather than a precedence rule.
    """
    with pytest.raises(ValidationError) as exc:
        ScrapeRequest(
            url="https://example.com", browser_engine="camoufox",
            spoof_os="macos", fingerprint_profile="windows_on_host",
        )

    assert "conflicts with" in str(exc.value)


def test_a_profile_on_another_engine_is_a_no_op_not_an_error():
    """A preset carrying a profile must stay usable when a caller overrides the
    engine — the same contract spoof_os already has."""
    req = ScrapeRequest(
        url="https://example.com", browser_engine="chromium",
        fingerprint_profile="windows_on_host",
    )

    assert req.fingerprint_profile == "windows_on_host"


def test_an_unknown_profile_name_is_rejected_at_the_edge():
    """The resolver degrades unknown names to `random`, which is right for a
    setting an operator typed once. A request field is different: the caller is
    present and can be told."""
    with pytest.raises(ValidationError):
        ScrapeRequest(url="https://example.com", fingerprint_profile="wintendo")


def test_a_profile_and_a_spoof_os_naming_the_same_os_are_accepted():
    """`windows_on_host` and `windows` claim the same OS.

    A first draft compared the literal strings and rejected this pair, which
    turned every spoof_os call on the two Camoufox presets — both of which state
    a profile — into a 400 for a request that had worked.
    """
    req = ScrapeRequest(
        url="https://example.com", browser_engine="camoufox",
        spoof_os="windows", fingerprint_profile="windows_on_host",
    )

    assert req.spoof_os == "windows"
    assert req.fingerprint_profile == "windows_on_host"


def test_a_profile_claiming_no_os_does_not_disagree_with_spoof_os():
    """`random` pins nothing, so it cannot contradict an OS.

    The profile still wins at launch, and meta.applied_fingerprint reports what
    ran — so nothing is dropped without the caller being able to see it, which
    is what the rejection was there to prevent.
    """
    req = ScrapeRequest(
        url="https://example.com", browser_engine="camoufox",
        spoof_os="windows", fingerprint_profile="random",
    )

    assert req.fingerprint_profile == "random"
