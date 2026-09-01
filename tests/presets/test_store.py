from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.presets.models import LocaleProfile, Preset
from src.presets.store import (
    BuiltInRegistry,
    FilePresetStore,
    PresetAlreadyExists,
    PresetNameInvalid,
    PresetNotFound,
    PresetReadOnly,
    PresetStore,
)


def _make_preset(
    name: str = "user_test",
    *,
    kind: str = "user",
    source: str = "custom",
    locales: dict | None = None,
    default_locale: str = "us",
    **overrides,
) -> Preset:
    if locales is None:
        locales = {"us": LocaleProfile(domain="com", country="US")}
    return Preset(
        name=name,
        source=source,
        kind=kind,
        request_defaults={"device": "desktop"},
        locales=locales,
        default_locale=default_locale,
        updated_at=1_700_000_000.0,
        **overrides,
    )


# ---------------------------------------------------------------- FilePresetStore


class TestFilePresetStore:
    def test_create_and_read_roundtrip(self, tmp_path: Path):
        store = FilePresetStore(base_path=tmp_path)
        original = _make_preset(name="user_amazon_extra")

        store.create(original)
        loaded = store.get("user_amazon_extra")
        assert loaded == original

        # file actually exists on disk
        assert (tmp_path / "user_amazon_extra.json").exists()

    def test_create_rejects_duplicate(self, tmp_path: Path):
        store = FilePresetStore(base_path=tmp_path)
        store.create(_make_preset(name="user_one"))
        with pytest.raises(PresetAlreadyExists):
            store.create(_make_preset(name="user_one"))

    def test_update_overwrites(self, tmp_path: Path):
        store = FilePresetStore(base_path=tmp_path)
        store.create(_make_preset(name="user_one"))

        updated = _make_preset(name="user_one", description="new")
        store.update("user_one", updated)
        loaded = store.get("user_one")
        assert loaded.description == "new"

    def test_update_missing_raises(self, tmp_path: Path):
        store = FilePresetStore(base_path=tmp_path)
        with pytest.raises(PresetNotFound):
            store.update("user_one", _make_preset(name="user_one"))

    def test_delete(self, tmp_path: Path):
        store = FilePresetStore(base_path=tmp_path)
        store.create(_make_preset(name="user_one"))
        store.delete("user_one")
        with pytest.raises(PresetNotFound):
            store.get("user_one")
        assert not (tmp_path / "user_one.json").exists()

    def test_delete_missing_raises(self, tmp_path: Path):
        store = FilePresetStore(base_path=tmp_path)
        with pytest.raises(PresetNotFound):
            store.delete("user_one")

    def test_list_returns_all(self, tmp_path: Path):
        store = FilePresetStore(base_path=tmp_path)
        store.create(_make_preset(name="user_a", source="amazon"))
        store.create(_make_preset(name="user_b", source="ebay"))
        names = sorted(p.name for p in store.list())
        assert names == ["user_a", "user_b"]

    def test_list_filter_by_source(self, tmp_path: Path):
        store = FilePresetStore(base_path=tmp_path)
        store.create(_make_preset(name="user_a", source="amazon"))
        store.create(_make_preset(name="user_b", source="ebay"))
        names = [p.name for p in store.list(source="amazon")]
        assert names == ["user_a"]

    def test_ignores_non_json_files(self, tmp_path: Path):
        store = FilePresetStore(base_path=tmp_path)
        (tmp_path / "README.md").write_text("not a preset")
        assert store.list() == []

    def test_skips_corrupt_files(self, tmp_path: Path):
        store = FilePresetStore(base_path=tmp_path)
        (tmp_path / "broken.json").write_text("{ not valid json")
        # corrupt files must not crash list(); they are just skipped
        assert store.list() == []


# ---------------------------------------------------------------- BuiltInRegistry


class TestBuiltInRegistry:
    def test_loads_bundled_jsons(self, tmp_path: Path):
        # write a fake built-in JSON
        preset_dict = _make_preset(
            name="fake_builtin", kind="builtin"
        ).model_dump(mode="json")
        (tmp_path / "fake_builtin.json").write_text(json.dumps(preset_dict))

        registry = BuiltInRegistry(base_path=tmp_path)
        items = registry.list()
        assert len(items) == 1
        assert items[0].name == "fake_builtin"
        assert items[0].kind == "builtin"

    def test_get_missing_raises(self, tmp_path: Path):
        registry = BuiltInRegistry(base_path=tmp_path)
        with pytest.raises(PresetNotFound):
            registry.get("unknown")

    def test_real_builtin_dir_has_at_least_one_preset(self):
        """Sanity check: shipped built-ins parse correctly."""
        registry = BuiltInRegistry()  # default path = src/presets/builtin/
        items = registry.list()
        names = {p.name for p in items}
        expected = {
            "amazon_product_chromium",
            "amazon_product_camoufox",
            "google_search_chromium",
            "google_search_camoufox",
            "amazon_search_chromium",
            "amazon_search_camoufox",
            "google_shopping_chromium",
            "google_shopping_camoufox",
            "ebay_search_chromium",
            "ebay_search_camoufox",
            "walmart_product_chromium",
            "walmart_product_camoufox",
            "youtube_video_chromium",
            "youtube_video_camoufox",
            "linkedin_profile_chromium",
            "linkedin_profile_camoufox",
        }
        assert expected.issubset(names)
        # every shipped built-in is kind="builtin" and parses cleanly
        for p in items:
            assert p.kind == "builtin"
            assert p.url_template, f"{p.name} missing url_template"
            assert p.locales, f"{p.name} missing locales"

        # Self-heal only fires when a *required* field comes back empty
        # (parser_pipeline._missing_required). A built-in that advertises
        # self_heal but marks nothing required can never heal — it would
        # silently return nulls forever. Pin: every self_heal built-in with
        # deterministic instructions must mark >=1 field required.
        by_name = {p.name: p for p in items}
        for name, p in by_name.items():
            if p.self_heal and p.parsing_instructions is not None:
                req_fields = [
                    f for f in p.parsing_instructions.fields.values()
                    if f.required
                ]
                assert req_fields, (
                    f"{name}: self_heal=true but no required field — "
                    f"self-heal can never trigger"
                )


# ---------------------------------------------------------------- PresetStore facade


class TestPresetStoreFacade:
    def test_resolves_builtin_first(self, tmp_path: Path):
        builtin_dir = tmp_path / "builtin"
        user_dir = tmp_path / "user"
        builtin_dir.mkdir()
        user_dir.mkdir()

        builtin = _make_preset(name="amazon_product", kind="builtin")
        (builtin_dir / "amazon_product.json").write_text(
            json.dumps(builtin.model_dump(mode="json"))
        )

        store = PresetStore(
            builtin=BuiltInRegistry(base_path=builtin_dir),
            user=FilePresetStore(base_path=user_dir),
        )
        found = store.get("amazon_product")
        assert found.kind == "builtin"

    def test_falls_back_to_user(self, tmp_path: Path):
        builtin_dir = tmp_path / "builtin"
        user_dir = tmp_path / "user"
        builtin_dir.mkdir()
        user_dir.mkdir()

        store = PresetStore(
            builtin=BuiltInRegistry(base_path=builtin_dir),
            user=FilePresetStore(base_path=user_dir),
        )
        store.create(_make_preset(name="user_custom"))
        found = store.get("user_custom")
        assert found.kind == "user"

    def test_create_rejects_builtin_name(self, tmp_path: Path):
        """User cannot shadow a built-in by reusing its name."""
        builtin_dir = tmp_path / "builtin"
        user_dir = tmp_path / "user"
        builtin_dir.mkdir()
        user_dir.mkdir()
        builtin = _make_preset(name="amazon_product", kind="builtin")
        (builtin_dir / "amazon_product.json").write_text(
            json.dumps(builtin.model_dump(mode="json"))
        )

        store = PresetStore(
            builtin=BuiltInRegistry(base_path=builtin_dir),
            user=FilePresetStore(base_path=user_dir),
        )
        with pytest.raises(PresetAlreadyExists):
            store.create(_make_preset(name="amazon_product"))

    def test_create_enforces_user_prefix(self, tmp_path: Path):
        """User-defined preset names must start with 'user_' to avoid
        accidental collisions with future built-ins."""
        builtin_dir = tmp_path / "builtin"
        user_dir = tmp_path / "user"
        builtin_dir.mkdir()
        user_dir.mkdir()

        store = PresetStore(
            builtin=BuiltInRegistry(base_path=builtin_dir),
            user=FilePresetStore(base_path=user_dir),
        )
        with pytest.raises(PresetNameInvalid):
            store.create(_make_preset(name="my_preset"))

    def test_update_and_delete_refuse_builtin(self, tmp_path: Path):
        builtin_dir = tmp_path / "builtin"
        user_dir = tmp_path / "user"
        builtin_dir.mkdir()
        user_dir.mkdir()
        builtin = _make_preset(name="amazon_product", kind="builtin")
        (builtin_dir / "amazon_product.json").write_text(
            json.dumps(builtin.model_dump(mode="json"))
        )

        store = PresetStore(
            builtin=BuiltInRegistry(base_path=builtin_dir),
            user=FilePresetStore(base_path=user_dir),
        )
        with pytest.raises(PresetReadOnly):
            store.update("amazon_product", builtin)
        with pytest.raises(PresetReadOnly):
            store.delete("amazon_product")

    def test_list_merges_builtin_and_user(self, tmp_path: Path):
        builtin_dir = tmp_path / "builtin"
        user_dir = tmp_path / "user"
        builtin_dir.mkdir()
        user_dir.mkdir()

        builtin = _make_preset(name="amazon_product", kind="builtin")
        (builtin_dir / "amazon_product.json").write_text(
            json.dumps(builtin.model_dump(mode="json"))
        )

        store = PresetStore(
            builtin=BuiltInRegistry(base_path=builtin_dir),
            user=FilePresetStore(base_path=user_dir),
        )
        store.create(_make_preset(name="user_one"))
        names = sorted(p.name for p in store.list())
        assert names == ["amazon_product", "user_one"]

    def test_list_filter_by_kind(self, tmp_path: Path):
        builtin_dir = tmp_path / "builtin"
        user_dir = tmp_path / "user"
        builtin_dir.mkdir()
        user_dir.mkdir()
        builtin = _make_preset(name="amazon_product", kind="builtin")
        (builtin_dir / "amazon_product.json").write_text(
            json.dumps(builtin.model_dump(mode="json"))
        )

        store = PresetStore(
            builtin=BuiltInRegistry(base_path=builtin_dir),
            user=FilePresetStore(base_path=user_dir),
        )
        store.create(_make_preset(name="user_one"))
        assert [p.name for p in store.list(kind="builtin")] == ["amazon_product"]
        assert [p.name for p in store.list(kind="user")] == ["user_one"]
