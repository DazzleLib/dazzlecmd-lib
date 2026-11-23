"""The catalogue of link and reparse-point kinds (core.safedel._linkkinds, 0.10.53).

classify_stat is pure: these tests feed it stat-like records, so every kind
can be tested on any machine without creating cloud files or app aliases.
"""
import os
import stat
import sys
from types import SimpleNamespace

import pytest

from dazzlecmd_lib.core.safedel import _linkkinds as K


def st(mode=stat.S_IFREG, attrs=0, tag=0):
    return SimpleNamespace(st_mode=mode, st_file_attributes=attrs, st_reparse_tag=tag)


R = K.FILE_ATTRIBUTE_REPARSE_POINT


@pytest.mark.parametrize("record, kind, readable, safe", [
    (st(attrs=R, tag=K.TAG_SYMLINK), "symlink", True, False),
    (st(stat.S_IFDIR, attrs=R | 0x10, tag=K.TAG_MOUNT_POINT), "junction", True, False),
    (st(attrs=R, tag=K.TAG_LX_SYMLINK), "wsl-symlink", False, False),
    (st(attrs=R, tag=K.TAG_APPEXECLINK), "app-alias", False, False),
    (st(attrs=R, tag=0x9000001A), "cloud-placeholder", False, False),
    (st(attrs=R, tag=0x9000101A), "cloud-placeholder", False, False),
    (st(attrs=R, tag=K.TAG_DEDUP), "dedup", False, True),
    (st(attrs=R, tag=0x12345678), "unknown-reparse", False, False),     # fails safe
    (st(), "file", False, True),
    (st(stat.S_IFDIR, attrs=0x10), "directory", False, True),
])
def test_each_kind_answers_the_four_questions(record, kind, readable, safe):
    k = K.classify_stat(record)
    assert (k.kind, k.target_readable, k.contents_safe) == (kind, readable, safe)
    assert bool(k.why_not_read) == (not safe)


@pytest.mark.parametrize("attr", [K.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS,
                                  K.FILE_ATTRIBUTE_RECALL_ON_OPEN, K.FILE_ATTRIBUTE_OFFLINE])
def test_a_file_whose_contents_are_not_local_is_never_read(attr):
    k = K.classify_stat(st(attrs=attr))
    assert not k.contents_safe and "online-only" in k.why_not_read
    assert not K.classify_stat(st(attrs=R | attr, tag=K.TAG_DEDUP)).contents_safe


def test_links_move_as_entries_but_placeholders_and_dedup_are_files():
    assert K.classify_stat(st(attrs=R, tag=K.TAG_LX_SYMLINK)).is_link
    assert not K.classify_stat(st(attrs=R, tag=0x9000001A)).is_link
    assert not K.classify_stat(st(attrs=R, tag=K.TAG_DEDUP)).is_link


def test_a_directory_symlink_reports_its_node_as_a_directory():
    assert K.classify_stat(st(attrs=R | 0x10, tag=K.TAG_SYMLINK)).node_is_dir


@pytest.mark.parametrize("attrs, tag, why", [
    (R | K.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS, 0x9000001A, "online-only"),
    (R, K.TAG_APPEXECLINK, "app execution alias"),
])
def test_copy_first_never_reads_an_item_it_must_not(tmp_path, monkeypatch, attrs, tag, why):
    """An online-only file must not be read (that downloads it); an app alias
    cannot be opened. Copy-first is skipped with a warning; the item still moves."""
    import dazzlecmd_lib.core.safedel as sd
    from dazzlecmd_lib.core.safedel import _copyfirst
    f = tmp_path / "work" / "item.bin"
    f.parent.mkdir()
    f.write_bytes(b"\0" * 10)
    real = K.classify_stat

    def fake(s):
        return real(SimpleNamespace(st_mode=s.st_mode, st_file_attributes=attrs, st_reparse_tag=tag))

    def no_read(*a, **k):
        raise AssertionError("copy-first read an item it must not read")

    monkeypatch.setattr(K, "describe", lambda p: fake(os.lstat(p)))
    monkeypatch.setattr(_copyfirst.shutil, "copy2", no_read)
    store = sd.TrashStore(store_path=str(tmp_path / "trash"), registry_path=str(tmp_path / "r.json"))
    result = store.trash([str(f)], copy_first=True, copy_first_max=10 ** 9)

    assert result.success, result.errors
    assert not f.exists()                                          # moved into the trash
    assert result.entries[0].copy_first is None
    assert any(why in w for w in result.warnings), result.warnings


def test_describe_on_real_entries(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert K.describe(str(f)).kind == "file"
    assert K.describe(str(tmp_path)).kind == "directory"
    assert K.describe(str(tmp_path / "missing")) is None
