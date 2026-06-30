"""Per-FQCN property store -- ``<fqcn>.<property>`` values addressed by
bang-path.

The store holds property values (channel verbosity, notes, env, ...) keyed
by the canonical FQCN bang-path from the path-operator grammar (SD-FQCN-1),
e.g. ``"dz:.kit.channels.verbosity"``. It is a thin wrapper over
:class:`~dazzlecmd_lib.config.ConfigManager` pointed at a separate,
discoverable ``properties.json`` -- the SAME machinery (atomic write, read
cache, per-aggregator isolation, ``DAZZLECMD_CONFIG`` test isolation), a
distinct file. ``ConfigManager`` is thereby a reusable "managed JSON file"
primitive; ``config.json`` and ``properties.json`` are instances.

Storage shape: ``properties.json`` is a flat JSON object whose keys ARE the
bang-paths (top-level), so ``set`` is a single-key atomic merge -- no
sub-dict read-modify-write.

The public API (``get`` / ``set`` / ``delete`` / ``list_prefix``) is the
stable contract; the JSON backend can later be swapped for the #77 overlay
tree without touching callers.
"""

from __future__ import annotations

from typing import Dict, Optional

from dazzlecmd_lib.config import ConfigManager


PROPERTIES_FILENAME = "properties.json"


class PropertyStore:
    """Read/write per-FQCN property values, backed by ``properties.json``.

    Instantiate with the aggregator's ``config_dir`` (the same one the
    engine passes to :class:`ConfigManager`) so properties land beside the
    aggregator's ``config.json`` (e.g. ``~/.wtf/properties.json``).
    """

    def __init__(self, config_dir=None, config: Optional[ConfigManager] = None):
        """Initialize.

        Args:
            config_dir: The aggregator's config directory. Forwarded to a
                dedicated ``ConfigManager`` for ``properties.json``.
                Ignored when ``config`` is supplied.
            config: An existing ``ConfigManager`` (already pointed at
                ``properties.json``) -- mainly for tests / injection.
        """
        if config is not None:
            self._cm = config
        else:
            self._cm = ConfigManager(
                config_dir=config_dir, filename=PROPERTIES_FILENAME
            )

    def get(self, bangpath, default=None):
        """Return the value stored at ``bangpath`` (``default`` if absent)."""
        value = self._cm.read().get(bangpath)
        return default if value is None else value

    def set(self, bangpath, value):
        """Set ``bangpath`` to ``value`` (atomic single-key merge)."""
        self._cm.write({bangpath: value})

    def delete(self, bangpath):
        """Remove ``bangpath`` if present. Returns True if it was present.

        Uses ``ConfigManager.replace`` (wholesale rewrite) because a merge
        ``write`` cannot remove a key.
        """
        data = dict(self._cm.read())
        if bangpath not in data:
            return False
        del data[bangpath]
        self._cm.replace(data)
        return True

    def list_prefix(self, prefix) -> Dict[str, object]:
        """Return ``{bangpath: value}`` for keys starting with ``prefix``.

        Skips bookkeeping keys (``_schema_version`` etc.). Useful for
        reading a node's whole property family, e.g.
        ``list_prefix("dz:.kit.channels")``.
        """
        return {
            k: v
            for k, v in self._cm.read().items()
            if k.startswith(prefix) and not k.startswith("_")
        }
