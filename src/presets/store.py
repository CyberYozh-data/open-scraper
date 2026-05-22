"""Persistence layer for presets.

Two backends:
  - `BuiltInRegistry` reads JSONs bundled at `src/presets/builtin/*.json`.
    Read-only.
  - `FilePresetStore` reads/writes JSONs under a mounted volume
    (`data/presets/*.json` in production, `tmp_path` in tests).

`PresetStore` is the public facade. `get()` resolves a name against built-ins
first, then user-defined presets; mutating calls (`create`/`update`/`delete`)
go to the user store and refuse to touch built-ins. User-created presets must
have a `user_` prefix to keep the namespace separate from future built-ins.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from src.presets.models import Preset, PresetKind

log = logging.getLogger(__name__)

USER_PREFIX = "user_"
DEFAULT_BUILTIN_DIR = Path(__file__).resolve().parent / "builtin"
DEFAULT_USER_DIR = Path("data/presets")


class PresetNotFound(KeyError):
    """No preset with this name in either store."""


class PresetAlreadyExists(ValueError):
    """A preset with this name already exists (built-in or user)."""


class PresetReadOnly(ValueError):
    """Attempted to mutate a built-in preset, or to create a user preset
    without the required `user_` prefix."""


class PresetNameInvalid(ValueError):
    """User preset name does not satisfy naming rules (e.g. missing prefix)."""


def _safe_name(name: str) -> str:
    """Defence-in-depth filename guard. The Pydantic validator already
    enforces snake_case, but we still want to refuse path-traversal attempts
    that might land here from a buggy caller.
    """
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"unsafe preset name: {name!r}")
    return name


def _read_preset_file(path: Path) -> Preset | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("preset read failed: %s (%s)", path, exc)
        return None
    try:
        data = json.loads(raw)
        return Preset.model_validate(data)
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("preset parse failed: %s (%s)", path, exc)
        return None


class BuiltInRegistry:
    """Read-only loader for bundled presets."""

    def __init__(self, base_path: Path | None = None) -> None:
        self.base_path = base_path or DEFAULT_BUILTIN_DIR

    def list(self, *, source: str | None = None) -> list[Preset]:
        if not self.base_path.exists():
            return []
        items: list[Preset] = []
        for path in sorted(self.base_path.glob("*.json")):
            preset = _read_preset_file(path)
            if preset is None:
                continue
            if source is not None and preset.source != source:
                continue
            items.append(preset)
        return items

    def get(self, name: str) -> Preset:
        _safe_name(name)
        path = self.base_path / f"{name}.json"
        if not path.exists():
            raise PresetNotFound(name)
        preset = _read_preset_file(path)
        if preset is None:
            raise PresetNotFound(name)
        return preset

    def exists(self, name: str) -> bool:
        # cheap existence check that avoids parsing the JSON
        try:
            _safe_name(name)
        except ValueError:
            return False
        return (self.base_path / f"{name}.json").is_file()


class FilePresetStore:
    """Read-write JSON-on-disk store for user-defined presets."""

    def __init__(self, base_path: Path | None = None) -> None:
        self.base_path = base_path or DEFAULT_USER_DIR

    def _path(self, name: str) -> Path:
        _safe_name(name)
        return self.base_path / f"{name}.json"

    def _ensure_dir(self) -> None:
        self.base_path.mkdir(parents=True, exist_ok=True)

    def list(self, *, source: str | None = None) -> list[Preset]:
        if not self.base_path.exists():
            return []
        items: list[Preset] = []
        for path in sorted(self.base_path.glob("*.json")):
            preset = _read_preset_file(path)
            if preset is None:
                continue
            if source is not None and preset.source != source:
                continue
            items.append(preset)
        return items

    def get(self, name: str) -> Preset:
        path = self._path(name)
        if not path.exists():
            raise PresetNotFound(name)
        preset = _read_preset_file(path)
        if preset is None:
            raise PresetNotFound(name)
        return preset

    def exists(self, name: str) -> bool:
        return self._path(name).exists()

    def create(self, preset: Preset) -> Preset:
        path = self._path(preset.name)
        self._ensure_dir()
        payload = json.dumps(
            preset.model_dump(mode="json"), ensure_ascii=False, indent=2
        )
        # `x` mode = exclusive create; raises FileExistsError if another writer
        # got here first. Closes the TOCTOU window between `exists()` + write.
        try:
            with open(path, "x", encoding="utf-8") as fh:
                fh.write(payload)
        except FileExistsError as exc:
            raise PresetAlreadyExists(preset.name) from exc
        return preset

    def update(self, name: str, preset: Preset) -> Preset:
        path = self._path(name)
        if not path.exists():
            raise PresetNotFound(name)
        payload = json.dumps(
            preset.model_dump(mode="json"), ensure_ascii=False, indent=2
        )
        # Write to a temp file in the same dir then os.replace — atomic on
        # the same filesystem, so a concurrent reader never sees a partial
        # file. NOTE: this does NOT serialize concurrent self-heal writers;
        # two workers healing the same user preset still race on `version`
        # (last-writer-wins). Acceptable for the single-host MVP; revisit
        # with a lock/CAS if multi-writer self-heal becomes common.
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
        return preset

    def delete(self, name: str) -> None:
        path = self._path(name)
        if not path.exists():
            raise PresetNotFound(name)
        path.unlink()


class PresetStore:
    """Facade over `BuiltInRegistry` + `FilePresetStore`.

    Read paths consult built-ins first (they win on name clashes), then user
    store. Write paths only touch the user store and refuse:
      - to overwrite a built-in name (PresetAlreadyExists),
      - to mutate a built-in (PresetReadOnly),
      - to create a user preset without the `user_` prefix (PresetReadOnly).
    """

    def __init__(
        self,
        *,
        builtin: BuiltInRegistry | None = None,
        user: FilePresetStore | None = None,
    ) -> None:
        self.builtin = builtin or BuiltInRegistry()
        self.user = user or FilePresetStore()

    def get(self, name: str) -> Preset:
        try:
            return self.builtin.get(name)
        except PresetNotFound:
            pass
        return self.user.get(name)

    def list(
        self,
        *,
        kind: PresetKind | None = None,
        source: str | None = None,
    ) -> list[Preset]:
        result: list[Preset] = []
        if kind in (None, "builtin"):
            result.extend(self.builtin.list(source=source))
        if kind in (None, "user"):
            result.extend(self.user.list(source=source))
        result.sort(key=lambda preset: preset.name)
        return result

    def create(self, preset: Preset) -> Preset:
        if self.builtin.exists(preset.name):
            raise PresetAlreadyExists(preset.name)
        if not preset.name.startswith(USER_PREFIX):
            raise PresetNameInvalid(
                f"user-defined preset name must start with {USER_PREFIX!r}: "
                f"got {preset.name!r}"
            )
        # Force kind="user" regardless of what the caller passed.
        normalized = preset.model_copy(update={"kind": "user"})
        return self.user.create(normalized)

    def update(self, name: str, preset: Preset) -> Preset:
        if self.builtin.exists(name):
            raise PresetReadOnly(f"cannot update built-in preset {name!r}")
        normalized = preset.model_copy(update={"kind": "user"})
        return self.user.update(name, normalized)

    def delete(self, name: str) -> None:
        if self.builtin.exists(name):
            raise PresetReadOnly(f"cannot delete built-in preset {name!r}")
        self.user.delete(name)
