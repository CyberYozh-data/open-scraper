from __future__ import annotations

import pytest

from src.presets.models import LocaleProfile, ParsingInstructions, Preset
from src.extract.models import FieldRule
from src.presets.materializer import (
    MaterializeError,
    SessionConflictError,
    PresetScrapeRequest,
    materialize,
)
from src.schemas import ScrapeRequest


def _amazon_preset(**overrides) -> Preset:
    base = {
        "name": "amazon_product",
        "source": "amazon",
        "kind": "builtin",
        "url_template": "https://www.amazon.{domain}/dp/{asin}",
        "request_defaults": {
            "device": "desktop",
            "proxy_type": "res_rotating",
            "stealth": True,
            "wait_until": "networkidle",
        },
        "locales": {
            "us": LocaleProfile(domain="com", country="US"),
            "de": LocaleProfile(domain="de", country="DE"),
        },
        "default_locale": "us",
        "parsing_instructions": ParsingInstructions(
            type="css",
            fields={"title": FieldRule(selector="#productTitle", required=True)},
        ),
        "updated_at": 1_700_000_000.0,
    }
    base.update(overrides)
    return Preset(**base)


class TestUrlTemplate:
    def test_fills_domain_and_params(self):
        preset = _amazon_preset()
        req = PresetScrapeRequest(
            source="amazon_product",
            preset_params={"asin": "B0CRTYZG5C"},
            locale="us",
        )
        scrape = materialize(preset, req)
        assert isinstance(scrape, ScrapeRequest)
        assert str(scrape.url) == "https://www.amazon.com/dp/B0CRTYZG5C"

    def test_locale_changes_domain(self):
        preset = _amazon_preset()
        req = PresetScrapeRequest(
            source="amazon_product",
            preset_params={"asin": "B0CRTYZG5C"},
            locale="de",
        )
        scrape = materialize(preset, req)
        assert str(scrape.url) == "https://www.amazon.de/dp/B0CRTYZG5C"

    def test_locale_lr_fills_template(self):
        """A locale's `lr` region code is exposed to url_template as {lr}."""
        preset = _amazon_preset(
            url_template="https://yandex.ru/search/?text={query}&lr={lr}",
            locales={
                "ru": LocaleProfile(domain="ru", country="RU", lr="225"),
                "us": LocaleProfile(domain="ru", country="RU", lr="84"),
            },
            default_locale="ru",
        )
        scrape = materialize(
            preset, PresetScrapeRequest(source="amazon_product", preset_params={"query": "x"}, locale="us")
        )
        assert str(scrape.url) == "https://yandex.ru/search/?text=x&lr=84"
        # proxy still routes through the locale country (RU), not the lr region.
        assert scrape.proxy_geo.country_code == "RU"

    def test_preset_param_overrides_locale_lr(self):
        preset = _amazon_preset(
            url_template="https://yandex.ru/search/?text={query}&lr={lr}",
            locales={"ru": LocaleProfile(domain="ru", country="RU", lr="225")},
            default_locale="ru",
        )
        scrape = materialize(
            preset,
            PresetScrapeRequest(
                source="amazon_product", preset_params={"query": "x", "lr": "10174"}, locale="ru"
            ),
        )
        assert str(scrape.url) == "https://yandex.ru/search/?text=x&lr=10174"

    def test_default_locale_used_when_unset(self):
        preset = _amazon_preset()
        req = PresetScrapeRequest(
            source="amazon_product", preset_params={"asin": "X"}
        )
        scrape = materialize(preset, req)
        assert "amazon.com" in str(scrape.url)

    def test_missing_param_raises_materialize_error(self):
        preset = _amazon_preset()
        req = PresetScrapeRequest(source="amazon_product", locale="us")
        with pytest.raises(MaterializeError) as exc:
            materialize(preset, req)
        assert "asin" in str(exc.value)

    def test_unknown_locale_raises(self):
        preset = _amazon_preset()
        req = PresetScrapeRequest(
            source="amazon_product",
            preset_params={"asin": "X"},
            locale="zz",
        )
        with pytest.raises(MaterializeError) as exc:
            materialize(preset, req)
        assert "zz" in str(exc.value)

    def test_lang_and_country_template_vars(self):
        preset = _amazon_preset(
            url_template="https://www.google.{domain}/search?q={query}&gl={country}&hl={lang}",
            locales={"de": LocaleProfile(domain="de", country="DE")},
            default_locale="de",
        )
        req = PresetScrapeRequest(
            source="amazon_product",
            preset_params={"query": "kaffee"},
            locale="de",
        )
        scrape = materialize(preset, req)
        url = str(scrape.url)
        assert "google.de" in url
        assert "q=kaffee" in url
        assert "gl=de" in url  # country lowercased for gl=
        assert "hl=de" in url  # language derived from locale


class TestRequestDefaults:
    def test_request_defaults_applied(self):
        preset = _amazon_preset()
        req = PresetScrapeRequest(
            source="amazon_product", preset_params={"asin": "X"}, locale="us"
        )
        scrape = materialize(preset, req)
        assert scrape.proxy_type == "res_rotating"
        assert scrape.wait_until == "networkidle"
        assert scrape.stealth is True

    def test_request_defaults_carry_max_retries(self):
        preset = _amazon_preset(
            request_defaults={"proxy_type": "res_rotating", "max_retries": 7}
        )
        req = PresetScrapeRequest(
            source="amazon_product", preset_params={"asin": "X"}, locale="us"
        )
        scrape = materialize(preset, req)
        assert scrape.max_retries == 7

    def test_locale_sets_proxy_geo(self):
        preset = _amazon_preset()
        req = PresetScrapeRequest(
            source="amazon_product", preset_params={"asin": "X"}, locale="de"
        )
        scrape = materialize(preset, req)
        assert scrape.proxy_geo is not None
        assert scrape.proxy_geo.country_code == "DE"

    def test_request_override_wins(self):
        preset = _amazon_preset()
        req = PresetScrapeRequest(
            source="amazon_product",
            preset_params={"asin": "X"},
            locale="us",
            request_override={"proxy_type": "none", "screenshot": True},
        )
        scrape = materialize(preset, req)
        assert scrape.proxy_type == "none"
        assert scrape.screenshot is True

    def test_unknown_request_default_key_raises_materialize_error(self):
        preset = _amazon_preset(
            request_defaults={"device": "desktop", "bogus_field": 123}
        )
        req = PresetScrapeRequest(
            source="amazon_product", preset_params={"asin": "X"}, locale="us"
        )
        with pytest.raises(MaterializeError):
            materialize(preset, req)


class TestParsing:
    def test_parsing_instructions_become_extract(self):
        preset = _amazon_preset()
        req = PresetScrapeRequest(
            source="amazon_product", preset_params={"asin": "X"}, locale="us"
        )
        scrape = materialize(preset, req)
        assert scrape.extract is not None
        assert "title" in scrape.extract.fields

    def test_parsing_override_replaces_preset(self):
        preset = _amazon_preset()
        req = PresetScrapeRequest(
            source="amazon_product",
            preset_params={"asin": "X"},
            locale="us",
            parsing_override=ParsingInstructions(
                type="css",
                fields={"custom": FieldRule(selector=".x")},
            ),
        )
        scrape = materialize(preset, req)
        assert "custom" in scrape.extract.fields
        assert "title" not in scrape.extract.fields

    def test_preset_meta_attached(self):
        preset = _amazon_preset()
        req = PresetScrapeRequest(
            source="amazon_product", preset_params={"asin": "X"}, locale="us"
        )
        scrape = materialize(preset, req)
        assert scrape.preset_meta is not None
        assert scrape.preset_meta.name == "amazon_product"
        assert scrape.preset_meta.source == "amazon"
        assert scrape.preset_meta.locale == "us"


class TestUrlEncoding:
    def test_path_param_uses_percent_encoding_not_plus(self):
        preset = _amazon_preset(
            url_template="https://www.amazon.{domain}/s/{term}"
        )
        req = PresetScrapeRequest(
            source="amazon_product",
            preset_params={"term": "red shoes"},
            locale="us",
        )
        scrape = materialize(preset, req)
        # space in a PATH segment must be %20, never +
        assert "/s/red%20shoes" in str(scrape.url)
        assert "+" not in str(scrape.url)

    def test_query_param_uses_plus_encoding(self):
        preset = _amazon_preset(
            url_template="https://www.google.{domain}/search?q={query}",
            locales={"us": LocaleProfile(domain="com", country="US")},
            default_locale="us",
        )
        req = PresetScrapeRequest(
            source="amazon_product",
            preset_params={"query": "red shoes"},
            locale="us",
        )
        scrape = materialize(preset, req)
        # space in QUERY may be + (application/x-www-form-urlencoded)
        assert "q=red+shoes" in str(scrape.url)


class TestProxyGeoOverride:
    def test_proxy_geo_from_request_defaults_logs(self, caplog):
        import logging

        preset = _amazon_preset(
            request_defaults={
                "device": "desktop",
                "proxy_geo": {"country_code": "NL"},
            }
        )
        req = PresetScrapeRequest(
            source="amazon_product", preset_params={"asin": "X"}, locale="de"
        )
        with caplog.at_level(logging.INFO):
            scrape = materialize(preset, req)
        # escape hatch honored ...
        assert scrape.proxy_geo.country_code == "NL"
        # ... but not silently
        assert any(
            "proxy_geo" in r.message and "NL" in r.message and "DE" in r.message
            for r in caplog.records
        )


class TestProxyCountry:
    """LocaleProfile.proxy_country decouples the proxy exit country from the
    locale's market country (LocaleProfile.country). Some markets must not be
    fetched through their own country's proxy exit (Google 429s the US
    residential range), so a locale can pin a market (gl=/hl=) while exiting
    through a different country.
    """

    def test_unset_proxy_country_falls_back_to_market_country(self):
        preset = _amazon_preset()
        req = PresetScrapeRequest(
            source="amazon_product", preset_params={"asin": "X"}, locale="de"
        )
        scrape = materialize(preset, req)
        assert scrape.proxy_geo.country_code == "DE"

    def test_proxy_country_decouples_exit_from_market(self):
        preset = _amazon_preset(
            url_template="https://www.google.{domain}/search?q={query}&gl={country}&hl={lang}",
            locales={
                "us": LocaleProfile(domain="com", country="US", proxy_country="GB"),
            },
            default_locale="us",
        )
        req = PresetScrapeRequest(
            source="amazon_product", preset_params={"query": "x"}, locale="us"
        )
        scrape = materialize(preset, req)
        # Exit country follows the override ...
        assert scrape.proxy_geo.country_code == "GB"
        # ... but the market (gl=/hl=, and the domain) still reflects the
        # locale's country, not the proxy exit. This is the assertion that
        # would catch a naive fix that repoints the market too.
        url = str(scrape.url)
        assert "google.com" in url
        assert "gl=us" in url
        assert "hl=en" in url

    def test_explicit_proxy_geo_overrides_proxy_country(self):
        """The request_defaults/request_override escape hatch still wins,
        even when the locale also sets proxy_country."""
        preset = _amazon_preset(
            request_defaults={
                "device": "desktop",
                "proxy_geo": {"country_code": "NL"},
            },
            locales={
                "us": LocaleProfile(domain="com", country="US", proxy_country="GB"),
            },
            default_locale="us",
        )
        req = PresetScrapeRequest(
            source="amazon_product", preset_params={"asin": "X"}, locale="us"
        )
        scrape = materialize(preset, req)
        assert scrape.proxy_geo.country_code == "NL"

    def test_derived_fields_follow_market_not_exit_on_non_degenerate_pair(self):
        """US/GB (the shipped google_* pair) is degenerate on every axis
        `derive_locale_profile()`/`_price_locale_for()` touch: both give
        hl=en and price locale "us", so a naive refactor that repoints
        either at the proxy exit instead of the market would still pass
        every US/GB-based assertion above. DE market / GB exit is not
        degenerate: it pins hl=, price-locale, and proxy exit to three
        different, independently-checkable values."""
        from src.extract.models import PostProcess

        preset = _amazon_preset(
            url_template="https://www.google.{domain}/search?q={query}&gl={country}&hl={lang}",
            locales={
                "de": LocaleProfile(domain="de", country="DE", proxy_country="GB"),
            },
            default_locale="de",
            parsing_instructions=ParsingInstructions(
                type="css",
                fields={
                    "price": FieldRule(
                        selector=".price",
                        post_process=[PostProcess(op="parse_price")],
                    ),
                },
            ),
        )
        req = PresetScrapeRequest(
            source="amazon_product", preset_params={"query": "x"}, locale="de"
        )
        scrape = materialize(preset, req)
        # market (DE) drives the derived language, not the exit (GB) — a
        # flip to en would silently mis-render hl= for every non-English
        # market that has a proxy_country override.
        assert "hl=de" in str(scrape.url)
        # market drives price locale too: a flip to "us" would parse
        # "12,34 €" as 1234.0 instead of 12.34.
        assert scrape.extract.fields["price"].post_process[0].args == ["eu"]
        # exit still follows proxy_country.
        assert scrape.proxy_geo.country_code == "GB"


class TestGoogleBuiltinPresetsExitViaGB:
    """The shipped google_search / google_shopping presets flag the `us`
    locale's proxy_country as GB: Google hard-blocks the proxy pool's US
    residential exit range (429 'unusual traffic') while still wanting the
    US market (gl=us)."""

    @pytest.mark.parametrize(
        "preset_file", ["google_search.json", "google_shopping.json"]
    )
    def test_us_locale_resolves_to_gb_exit_with_us_market(self, preset_file):
        import json
        import pathlib

        raw = json.loads(
            pathlib.Path(f"src/presets/builtin/{preset_file}").read_text()
        )
        preset = Preset(**raw)
        scrape = materialize(
            preset,
            PresetScrapeRequest(
                source=preset.name,
                preset_params={"query": "test"},
                locale="us",
            ),
        )
        assert scrape.proxy_geo.country_code == "GB"
        url = str(scrape.url)
        assert "google.com" in url
        assert "gl=us" in url


class TestAiOnlyPreset:
    def test_ai_only_preset_has_no_extract(self):
        preset = _amazon_preset(
            parsing_instructions=None,
            llm_extract_prompt="Extract product as JSON",
        )
        req = PresetScrapeRequest(
            source="amazon_product", preset_params={"asin": "X"}, locale="us"
        )
        scrape = materialize(preset, req)
        assert scrape.extract is None
        # raw_html is forced on so PR-3 LLM step has something to work with
        assert scrape.raw_html is True


class TestParsePriceLocaleInjection:
    def _preset_with_price(self, **over):
        from src.extract.models import PostProcess

        return _amazon_preset(
            parsing_instructions=ParsingInstructions(
                type="css",
                fields={
                    "price": FieldRule(
                        selector=".a-offscreen",
                        post_process=[PostProcess(op="parse_price")],
                    ),
                    "price_eu_explicit": FieldRule(
                        selector=".x",
                        post_process=[PostProcess(op="parse_price", args=["eu"])],
                    ),
                    "rating": FieldRule(
                        selector=".r",
                        post_process=[PostProcess(op="parse_float")],
                    ),
                },
            ),
            **over,
        )

    def test_us_locale_injects_us(self):
        preset = self._preset_with_price()
        req = PresetScrapeRequest(
            source="amazon_product", preset_params={"asin": "X"}, locale="us"
        )
        scrape = materialize(preset, req)
        pp = scrape.extract.fields["price"].post_process[0]
        assert pp.op == "parse_price"
        assert pp.args == ["us"]

    def test_de_locale_injects_eu(self):
        preset = self._preset_with_price()
        req = PresetScrapeRequest(
            source="amazon_product", preset_params={"asin": "X"}, locale="de"
        )
        scrape = materialize(preset, req)
        assert scrape.extract.fields["price"].post_process[0].args == ["eu"]

    def test_explicit_args_preserved(self):
        preset = self._preset_with_price()
        req = PresetScrapeRequest(
            source="amazon_product", preset_params={"asin": "X"}, locale="us"
        )
        scrape = materialize(preset, req)
        # author set ["eu"] explicitly — must not be overwritten by US locale
        assert scrape.extract.fields["price_eu_explicit"].post_process[0].args == ["eu"]

    def test_non_parse_price_ops_untouched(self):
        preset = self._preset_with_price()
        req = PresetScrapeRequest(
            source="amazon_product", preset_params={"asin": "X"}, locale="de"
        )
        scrape = materialize(preset, req)
        assert scrape.extract.fields["rating"].post_process[0].op == "parse_float"
        assert scrape.extract.fields["rating"].post_process[0].args == []

    def test_parsing_override_also_gets_locale(self):
        preset = self._preset_with_price()
        from src.extract.models import PostProcess

        req = PresetScrapeRequest(
            source="amazon_product",
            preset_params={"asin": "X"},
            locale="de",
            parsing_override=ParsingInstructions(
                type="css",
                fields={
                    "p": FieldRule(
                        selector=".p",
                        post_process=[PostProcess(op="parse_price")],
                    )
                },
            ),
        )
        scrape = materialize(preset, req)
        assert scrape.extract.fields["p"].post_process[0].args == ["eu"]


class TestParserPlan:
    def test_plan_carries_preset_self_heal_and_identity(self):
        preset = _amazon_preset(self_heal=True, version=4)
        req = PresetScrapeRequest(
            source="amazon_product",
            preset_params={"asin": "X"},
            locale="us",
            llm={"model": "openai/gpt-5.4-mini"},
        )
        scrape = materialize(preset, req)
        assert scrape.parser_plan is not None
        plan = scrape.parser_plan
        assert plan.self_heal is True
        assert plan.llm_model == "openai/gpt-5.4-mini"
        assert plan.preset_name == "amazon_product"
        assert plan.preset_kind == "builtin"

    def test_request_self_heal_overrides_preset(self):
        preset = _amazon_preset(self_heal=True)
        req = PresetScrapeRequest(
            source="amazon_product",
            preset_params={"asin": "X"},
            locale="us",
            self_heal=False,
            llm={"model": "m"},
        )
        scrape = materialize(preset, req)
        assert scrape.parser_plan.self_heal is False

    def test_no_llm_means_llm_model_none(self):
        preset = _amazon_preset(self_heal=True)
        req = PresetScrapeRequest(
            source="amazon_product", preset_params={"asin": "X"}, locale="us"
        )
        scrape = materialize(preset, req)
        # self_heal in preset but no llm config -> model None disables LLM
        assert scrape.parser_plan.llm_model is None

    def test_schema_override_lands_in_plan(self):
        preset = _amazon_preset(output_schema={"a": {"type": "string"}})
        req = PresetScrapeRequest(
            source="amazon_product",
            preset_params={"asin": "X"},
            locale="us",
            schema_override={"b": {"type": "number"}},
            llm={"model": "m"},
        )
        scrape = materialize(preset, req)
        assert scrape.parser_plan.output_schema == {"b": {"type": "number"}}

    def test_ai_only_prompt_in_plan(self):
        preset = _amazon_preset(
            parsing_instructions=None,
            llm_extract_prompt="grab title",
        )
        req = PresetScrapeRequest(
            source="amazon_product",
            preset_params={"asin": "X"},
            locale="us",
            llm={"model": "m"},
        )
        scrape = materialize(preset, req)
        assert scrape.parser_plan.llm_extract_prompt == "grab title"


class TestMaterializeSessionId:
    def test_session_id_field_propagates(self):
        sr = materialize(
            _amazon_preset(),
            PresetScrapeRequest(
                source="amazon_product",
                preset_params={"asin": "X"},
                session_id="sess_abc",
            ),
        )
        assert sr.session_id == "sess_abc"

    def test_no_session_id_is_none(self):
        sr = materialize(
            _amazon_preset(),
            PresetScrapeRequest(source="amazon_product", preset_params={"asin": "X"}),
        )
        assert sr.session_id is None

    def test_session_id_via_request_override_propagates(self):
        sr = materialize(
            _amazon_preset(),
            PresetScrapeRequest(
                source="amazon_product",
                preset_params={"asin": "X"},
                request_override={"session_id": "sess_ovr"},
            ),
        )
        assert sr.session_id == "sess_ovr"

    def test_field_and_override_equal_ok(self):
        sr = materialize(
            _amazon_preset(),
            PresetScrapeRequest(
                source="amazon_product",
                preset_params={"asin": "X"},
                session_id="sess_same",
                request_override={"session_id": "sess_same"},
            ),
        )
        assert sr.session_id == "sess_same"

    def test_field_and_override_diverge_raises(self):
        with pytest.raises(SessionConflictError, match="session_id conflict"):
            materialize(
                _amazon_preset(),
                PresetScrapeRequest(
                    source="amazon_product",
                    preset_params={"asin": "X"},
                    session_id="sess_field",
                    request_override={"session_id": "sess_override"},
                ),
            )
        assert issubclass(SessionConflictError, MaterializeError)


def test_yandex_preset_uses_prem_and_warmup():
    import json, pathlib
    from src.presets.models import Preset
    from src.presets.materializer import materialize, PresetScrapeRequest

    raw = json.loads(
        pathlib.Path("src/presets/builtin/yandex_search.json").read_text()
    )
    preset = Preset(**raw)
    out = materialize(preset, PresetScrapeRequest(source="yandex_search", locale="ru", preset_params={"query": "купить ноутбук"}))
    assert out.proxy_type == "prem_res_rotating"
    assert out.prem_proxy_options.ip_filter == "quality-security"
    assert out.warmup.type == "homepage"
    assert out.proxy_geo.country_code == "RU"
    assert out.browser_engine == "camoufox"
    # Yandex serves a transparent JS "browser check" interstitial
    # (/showcaptchafast) that self-resolves and redirects to the real SERP a
    # few seconds after domcontentloaded fires. Without wait_for_selector we
    # snapshot the interstitial instead of the SERP. Preset/LocaleProfile use
    # pydantic's default extra="ignore", so e.g. a misplaced key (outside
    # request_defaults) would vanish silently instead of raising — this
    # assertion is what stands between the fix and a silent regression.
    assert out.wait_for_selector == "li.serp-item"
