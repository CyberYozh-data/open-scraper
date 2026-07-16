"""Guard that every builtin preset validates. `_read_preset_file` swallows
validation errors and drops the preset from the registry, so a malformed builtin
would silently vanish rather than fail CI; this catches that."""
from __future__ import annotations

import glob
import json
import os

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
