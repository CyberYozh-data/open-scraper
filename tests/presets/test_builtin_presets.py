"""Guard that every builtin preset validates. `_read_preset_file` swallows
validation errors and drops the preset from the registry, so a malformed builtin
would silently vanish rather than fail CI; this catches that."""
from __future__ import annotations

import glob
import json
import os
import time

import pytest

from src.presets.models import Preset
from src.presets.store import DEFAULT_BUILTIN_DIR

BUILTIN_FILES = sorted(glob.glob(os.path.join(str(DEFAULT_BUILTIN_DIR), "*.json")))


def test_builtin_dir_not_empty():
    assert BUILTIN_FILES, "no builtin presets found"


@pytest.mark.parametrize("path", BUILTIN_FILES, ids=lambda p: os.path.basename(p))
def test_builtin_preset_validates(path):
    with open(path, encoding="utf-8") as fh:
        Preset(**json.load(fh))


# (preset base name, field, declared item/property type) for every builtin field
# MEASURED to come back null on a self-healing preset. `output_schema` is handed
# to the self-heal LLM as the target contract (parser_pipeline.run ->
# generate_selectors), so a schema claiming a field can never be null steers a
# heal against the field's only observed behaviour. Reverting any of these to a
# bare scalar type left the suite fully green before this test existed -- the
# same gap `amazon_search`'s own schema test was written to close.
NULLABLE_BY_MEASUREMENT = [
    # rating null 3/12 (2026-08-27) and 4/12 (2026-08-28): its "out of" regex is
    # English-only and Amazon serves German on the de locale.
    ("amazon_product", "rating", ["number", "null"]),
    # headline null 4/4 in BOTH audit records.
    ("linkedin_profile", "headline", ["string", "null"]),
    # By design, per amazon_search's row-alignment invariant: a present-but-empty
    # container must null IN PLACE rather than shrink the array.
    ("amazon_search", "prices", ["number", "null"]),
    ("amazon_search", "ratings", ["number", "null"]),
]


@pytest.mark.parametrize("base,field,expected", NULLABLE_BY_MEASUREMENT)
@pytest.mark.parametrize("engine", ["chromium", "camoufox"])
def test_measured_nullable_fields_declare_null(base, field, expected, engine):
    path = os.path.join(str(DEFAULT_BUILTIN_DIR), f"{base}_{engine}.json")
    with open(path, encoding="utf-8") as fh:
        preset = json.load(fh)
    assert preset["self_heal"] is True, "this guard only matters on a healing preset"
    prop = preset["output_schema"]["properties"][field]
    declared = prop["items"]["type"] if prop.get("type") == "array" else prop["type"]
    assert declared == expected


@pytest.mark.parametrize("path", BUILTIN_FILES, ids=lambda p: os.path.basename(p))
def test_builtin_updated_at_is_not_in_the_future(path):
    """A stamp later than now is a typo, never a fact -- these are hand-edited
    constants recording when a recipe last changed, and a preset cannot have
    been updated after the moment it is read.

    Written because the fix wave that added it shipped `1787961600.0` on six
    presets, which is 2026-08-29 00:00 UTC: a day in the FUTURE, on a branch
    whose whole subject was wrong numbers. It survived the entire suite, and
    the review caught it by hand. This is the check that would not have needed
    a human."""
    with open(path, encoding="utf-8") as fh:
        updated_at = json.load(fh)["updated_at"]
    now = time.time()
    assert updated_at <= now, (
        f"{os.path.basename(path)} claims updated_at={updated_at} "
        f"({time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(updated_at))}), "
        f"which is in the future (now {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(now))})"
    )
