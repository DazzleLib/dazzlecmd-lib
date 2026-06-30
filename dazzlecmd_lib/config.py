"""User config read/write for dazzlecmd-pattern aggregators.

Config file: ``~/.dazzlecmd/config.json`` (overridable via
``DAZZLECMD_CONFIG`` environment variable).

Schema (Phase 3+):
    {
        "_schema_version": 1,
        "kit_precedence": [...],
        "active_kits": [...],
        "disabled_kits": [...],
        "favorites": {"short": "fqcn", ...},
        "silenced_hints": {"tools": [...], "kits": [...]},
        "shadowed_tools": [...],
        "hidden_tools": [...],
        "kit_discovery": "auto"
    }

The visibility keys form a ladder (increasing suppression):
    - ``silenced_hints``: suppress the "did you mean" hints for a tool/kit;
      the tool still lists and dispatches.
    - ``hidden_tools``: omit the tool's FQCN from display (``dz list``/``dz
      tree``/help) but keep it fully dispatchable (short name still claimed).
      A RENDER-only filter -- revealed by ``--show-hidden``.
    - ``shadowed_tools``: remove the tool at discovery -- not listed, not
      dispatchable, and its short name is freed for another tool.

All keys are optional. Missing keys fall back to sensible defaults.
Malformed entries (wrong type, bad JSON) are tolerated with a stderr
warning and the malformed key is treated as absent.
"""

import json
import os
import sys

from dazzle_filekit.operations import atomic_write_json


SCHEMA_VERSION = 1


class ConfigManager:
    """Reads and writes an aggregator's config file with caching and
    atomic writes.

    Path resolution order (highest priority first):
        1. ``DAZZLECMD_CONFIG`` env var (points to full file path;
           used for test isolation across all aggregators)
        2. ``config_dir`` constructor argument + ``config.json``
        3. Default ``~/.dazzlecmd/config.json`` (back-compat)

    Per-aggregator isolation: each ``AggregatorEngine`` passes its own
    ``config_dir`` (typically ``~/.<command>``) so wtf-windows uses
    ``~/.wtf/config.json`` while dazzlecmd uses ``~/.dz/config.json``
    — they don't share kit precedence, favorites, or silencing.

    Instantiate once per engine and reuse.
    """

    def __init__(self, config_dir=None, filename="config.json"):
        """Initialize.

        Args:
            config_dir: Directory containing the managed file for this
                aggregator. If None, falls back to ``~/.dazzlecmd``.
                The ``DAZZLECMD_CONFIG`` env var, if set, overrides
                both.
            filename: The managed JSON file's name. Defaults to
                ``config.json``. Pass e.g. ``properties.json`` to manage a
                SIBLING file with the SAME machinery (atomic write, read
                cache, per-aggregator isolation). Sibling files resolve
                beside the config file -- including under the
                ``DAZZLECMD_CONFIG`` test-isolation override -- so one env
                var isolates every managed file in a test's temp dir.
        """
        self._cache = None
        self._config_dir_override = config_dir
        self._filename = filename

    def config_path(self):
        """Return the active managed-file path (lazy, env-overridable).

        ``DAZZLECMD_CONFIG`` points at the ``config.json`` file; a sibling
        managed file (e.g. ``properties.json``) resolves to that file's
        directory, so the single env var isolates every managed file in a
        test's temp dir.
        """
        override = os.environ.get("DAZZLECMD_CONFIG")
        if override:
            if self._filename == "config.json":
                return override
            return os.path.join(os.path.dirname(override), self._filename)
        if self._config_dir_override:
            return os.path.join(self._config_dir_override, self._filename)
        return os.path.expanduser(f"~/.dazzlecmd/{self._filename}")

    def config_dir(self):
        """Return the directory containing the active config file."""
        return os.path.dirname(self.config_path())

    def read(self):
        """Return the parsed config as a dict (cached after first read).

        Tolerates missing file, malformed JSON, and non-dict root.
        """
        if self._cache is not None:
            return self._cache

        path = self.config_path()
        if not os.path.isfile(path):
            self._cache = {}
            return self._cache

        try:
            # utf-8-sig: tolerate a UTF-8 BOM (e.g. PowerShell's
            # `Out-File -Encoding utf8` writes one); reads BOM-less
            # files identically. Write path stays plain utf-8.
            with open(path, "r", encoding="utf-8-sig") as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"Warning: could not read {path}: {exc}",
                file=sys.stderr,
            )
            self._cache = {}
            return self._cache

        if not isinstance(config, dict):
            print(
                f"Warning: {path} is not a JSON object, ignoring",
                file=sys.stderr,
            )
            self._cache = {}
            return self._cache

        self._cache = config
        return self._cache

    def get_list(self, key, default=None):
        """Return a list-valued config key, validated."""
        config = self.read()
        value = config.get(key)
        if value is None:
            return default
        if not isinstance(value, list):
            print(
                f"Warning: config key '{key}' is not a list, ignoring",
                file=sys.stderr,
            )
            return default
        return value

    def get_dict(self, key, default=None):
        """Return a dict-valued config key, validated."""
        config = self.read()
        value = config.get(key)
        if value is None:
            return default if default is not None else {}
        if not isinstance(value, dict):
            print(
                f"Warning: config key '{key}' is not a dict, ignoring",
                file=sys.stderr,
            )
            return default if default is not None else {}
        return value

    def write(self, updates):
        """Merge ``updates`` into the config and write atomically.

        Creates the config directory on first write. Injects
        ``_schema_version`` if missing. Invalidates the read cache.
        """
        path = self.config_path()

        existing = {}
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8-sig") as f:  # BOM-tolerant
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    existing = loaded
            except (json.JSONDecodeError, OSError):
                existing = {}

        existing.setdefault("_schema_version", SCHEMA_VERSION)
        existing.update(updates)
        self._atomic_write(existing)

    def replace(self, data):
        """Overwrite the ENTIRE file with ``data`` (wholesale, no merge).

        Unlike ``write`` (which merges ``updates`` into the existing
        file), ``replace`` rewrites from scratch -- use it to DELETE keys
        (write the map without them). Injects ``_schema_version`` if
        missing. Atomic; invalidates the read cache.
        """
        data = dict(data)
        data.setdefault("_schema_version", SCHEMA_VERSION)
        self._atomic_write(data)

    def _atomic_write(self, data):
        """Write ``data`` as the file's full content, atomically, and
        invalidate the cache.

        Delegates the temp-file-write + atomic ``os.replace`` (and parent
        dir creation) to ``dazzle_filekit.atomic_write_json`` so that file
        machinery lives in ONE place rather than being re-implemented here.
        ``default=None`` preserves the prior fail-loud behavior (a non-JSON
        value raises ``TypeError`` rather than being stringified).
        """
        atomic_write_json(self.config_path(), data, indent=4, default=None)
        self._cache = None

    def invalidate(self):
        """Clear the read cache so the next read() re-reads the file."""
        self._cache = None
