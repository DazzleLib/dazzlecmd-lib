"""A manifest says which version wrote it.

Through 0.10.43 every manifest carried the constant ``"0.1.0"``. From 0.10.44
``safedel_version`` is the package's base version, so a later reader can tell
which rules a batch was written under; ``"0.1.0"`` now means "0.10.43 or
earlier".

One file under ``tmp_path`` is deleted into a store under ``tmp_path``.
"""
import json
import os
import re

import dazzlecmd_lib.core.safedel as sd
from dazzlecmd_lib._version import get_base_version
from dazzlecmd_lib.core.safedel import _store


def _manifest_of_one_delete(tmp_path):
    victim = tmp_path / "work" / "a.txt"
    victim.parent.mkdir()
    victim.write_text("x")
    store = sd.TrashStore(store_path=str(tmp_path / "trash"),
                          registry_path=str(tmp_path / "reg.json"))
    result = store.trash([str(victim)])
    assert result.success, result.errors
    with open(os.path.join(str(tmp_path / "trash"), result.folder_name, "manifest.json"),
              encoding="utf-8") as fh:
        return json.load(fh)


def test_a_manifest_records_the_version_that_wrote_it(tmp_path):
    manifest = _manifest_of_one_delete(tmp_path)

    assert manifest["safedel_version"] == get_base_version()
    assert manifest["safedel_version"] != "0.1.0"                 # the old constant
    assert re.match(r"^\d+\.\d+\.\d+", manifest["safedel_version"])


def test_a_version_that_cannot_be_read_does_not_stop_a_delete(tmp_path, monkeypatch):
    import dazzlecmd_lib._version as v

    def broken():
        raise RuntimeError("no version for the test")

    monkeypatch.setattr(v, "get_base_version", broken)
    assert _store._writer_version() == "unknown"

    manifest = _manifest_of_one_delete(tmp_path)
    assert manifest["safedel_version"] == "unknown"
