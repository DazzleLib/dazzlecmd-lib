"""Tests for ``dazzlecmd_lib.property_store.PropertyStore`` and the
``ConfigManager`` ``filename=`` generalization it rides on (SD-FQCN-3).

Verifies the storage decision: a SEPARATE, discoverable ``properties.json``
backed by the SAME ``ConfigManager`` machinery (atomic write, cache,
per-aggregator isolation, ``DAZZLECMD_CONFIG`` test isolation) -- no second
implementation, no second env var.
"""

from __future__ import annotations

import json

from dazzlecmd_lib.config import ConfigManager
from dazzlecmd_lib.property_store import PROPERTIES_FILENAME, PropertyStore


class TestPropertyStoreRoundTrip:
    def test_set_get(self, tmp_path):
        store = PropertyStore(config_dir=str(tmp_path))
        store.set("dz:.kit.channels.verbosity", 4)
        assert store.get("dz:.kit.channels.verbosity") == 4

    def test_get_absent_returns_default(self, tmp_path):
        store = PropertyStore(config_dir=str(tmp_path))
        assert store.get("dz:.nope") is None
        assert store.get("dz:.nope", default=7) == 7

    def test_set_overwrites(self, tmp_path):
        store = PropertyStore(config_dir=str(tmp_path))
        store.set("dz:.x", 1)
        store.set("dz:.x", 2)
        assert store.get("dz:.x") == 2

    def test_string_and_json_values(self, tmp_path):
        store = PropertyStore(config_dir=str(tmp_path))
        store.set("dz:.note", "hello")
        store.set("dz:.list", [1, 2, 3])
        assert store.get("dz:.note") == "hello"
        assert store.get("dz:.list") == [1, 2, 3]


class TestPropertyStoreDelete:
    def test_delete_present_is_surgical(self, tmp_path):
        store = PropertyStore(config_dir=str(tmp_path))
        store.set("dz:.a", 1)
        store.set("dz:.b", 2)
        assert store.delete("dz:.a") is True
        assert store.get("dz:.a") is None
        assert store.get("dz:.b") == 2  # only the named key is removed

    def test_delete_absent(self, tmp_path):
        store = PropertyStore(config_dir=str(tmp_path))
        assert store.delete("dz:.nope") is False


class TestPropertyStoreListPrefix:
    def test_list_prefix_family(self, tmp_path):
        store = PropertyStore(config_dir=str(tmp_path))
        store.set("dz:.kit.channels.verbosity", 4)
        store.set("dz:.kit.channels.config.verbosity", 2)
        store.set("dz:.tool.channels.verbosity", 1)
        fam = store.list_prefix("dz:.kit.channels")
        assert fam == {
            "dz:.kit.channels.verbosity": 4,
            "dz:.kit.channels.config.verbosity": 2,
        }

    def test_list_prefix_skips_bookkeeping(self, tmp_path):
        store = PropertyStore(config_dir=str(tmp_path))
        store.set("dz:.x", 1)
        # ConfigManager.write injects _schema_version; it must not leak.
        assert "_schema_version" not in store.list_prefix("")
        assert store.list_prefix("") == {"dz:.x": 1}


class TestSeparateFileSharedMachinery:
    def test_properties_live_in_properties_json(self, tmp_path):
        store = PropertyStore(config_dir=str(tmp_path))
        store.set("dz:.x", 1)
        path = tmp_path / PROPERTIES_FILENAME
        assert path.is_file()
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["dz:.x"] == 1

    def test_properties_do_not_touch_config_json(self, tmp_path):
        cfg = ConfigManager(config_dir=str(tmp_path))
        cfg.write({"active_kits": ["media"]})
        store = PropertyStore(config_dir=str(tmp_path))
        store.set("dz:.x", 1)
        cfg.invalidate()
        # config.json is untouched; the property is NOT in it.
        assert cfg.read().get("active_kits") == ["media"]
        assert "dz:.x" not in cfg.read()
        assert (tmp_path / "config.json").is_file()
        assert (tmp_path / PROPERTIES_FILENAME).is_file()


class TestConfigManagerFilenameBackCompat:
    def test_default_filename_is_config_json(self, tmp_path):
        cm = ConfigManager(config_dir=str(tmp_path))
        assert cm.config_path().endswith("config.json")

    def test_env_override_targets_config_json_directly(self, tmp_path, monkeypatch):
        target = tmp_path / "custom-config.json"
        monkeypatch.setenv("DAZZLECMD_CONFIG", str(target))
        cm = ConfigManager()
        assert cm.config_path() == str(target)

    def test_env_override_resolves_sibling_beside_config(self, tmp_path, monkeypatch):
        target = tmp_path / "custom-config.json"
        monkeypatch.setenv("DAZZLECMD_CONFIG", str(target))
        sibling = ConfigManager(filename=PROPERTIES_FILENAME)
        assert sibling.config_path() == str(tmp_path / PROPERTIES_FILENAME)

    def test_replace_deletes_wholesale(self, tmp_path):
        cm = ConfigManager(config_dir=str(tmp_path), filename=PROPERTIES_FILENAME)
        cm.write({"a": 1, "b": 2})
        cm.replace({"a": 1})  # 'b' dropped
        cm.invalidate()
        data = cm.read()
        assert data.get("a") == 1
        assert "b" not in data


class TestPerAggregatorIsolation:
    def test_separate_config_dirs_dont_share(self, tmp_path):
        dz = PropertyStore(config_dir=str(tmp_path / "dz"))
        wtf = PropertyStore(config_dir=str(tmp_path / "wtf"))
        dz.set("dz:.x", 1)
        wtf.set("dz:.x", 99)
        assert dz.get("dz:.x") == 1
        assert wtf.get("dz:.x") == 99
