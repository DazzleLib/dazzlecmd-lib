"""Recovery puts things back by MOVING them when it can (0.10.40).

Through 0.10.39, recovering a folder copied it back and deleted the stored
copy: every file and subfolder inside came back as a NEW file, with a new
creation time. Now recovery renames first, as staging does. On one drive a
rename is a single directory-entry change, so a folder returns with its
contents exactly as stored. Only across drives (``EXDEV``) is it copied, and
then recovery says so. Any other failure copies nothing and leaves the
content in the trash.

After recovering, recovery compares the item's timestamps with the record
and prints ``times verified`` or a warning; a metadata failure is reported,
no longer swallowed.

"The same file" is proven by identity, not by time: a renamed file keeps its
file id (``st_ino``), a copied one gets a new id. Creation time is compared
too where the platform has one.

Rule 1b: everything is under ``tmp_path``. The tests that make a rename fail
replace ``os.rename`` only for paths inside the test's own trash folder.
"""
import errno
import json
import os
import sys

import pytest

import dazzlecmd_lib.core.safedel as sd
from dazzlecmd_lib.core.safedel import _recover


def _store_in(tmp_path, **kw):
    return sd.TrashStore(store_path=str(tmp_path / "trash"),
                         registry_path=str(tmp_path / "reg.json"), **kw)


def _write(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _identity(path):
    st = os.lstat(path)
    born = getattr(st, "st_birthtime_ns", None)
    if born is None and sys.platform == "win32":
        born = st.st_ctime_ns
    return (st.st_ino, st.st_mtime_ns, born)


def _tree(tmp_path):
    root = tmp_path / "src" / "tree"
    files = [_write(root / "one.txt", b"1"),
             _write(root / "sub" / "two.txt", b"22"),
             _write(root / "sub" / "deep" / "three.txt", b"333")]
    return root, files, [root / "sub", root / "sub" / "deep"]


def _rename_fails_in(monkeypatch, trash_dir, exc):
    """Make os.rename raise ``exc`` for sources inside ``trash_dir`` only."""
    real = os.rename
    prefix = os.path.normcase(str(trash_dir)) + os.sep

    def fake(src, dst, *a, **k):
        if os.path.normcase(str(src)).startswith(prefix):
            raise exc
        return real(src, dst, *a, **k)
    monkeypatch.setattr(_recover.os, "rename", fake)


# -- on one drive: the same files come back ----------------------------------


def test_a_recovered_folder_holds_the_very_same_files(tmp_path, capsys):
    root, files, folders = _tree(tmp_path)
    everything = files + folders + [root]
    before = {str(p): _identity(p) for p in everything}
    store = _store_in(tmp_path)
    result = store.trash([str(root)])
    assert result.success and not root.exists()

    rc = sd.recover_folder(store, result.folder_name)
    out = capsys.readouterr().out

    assert rc == 0
    after = {str(p): _identity(p) for p in everything}
    assert after == before                    # same file ids, same times: nothing was re-created
    assert "times verified" in out
    assert "moved back whole" in out


def test_a_recovered_file_is_the_same_file(tmp_path, capsys):
    f = _write(tmp_path / "src" / "a.txt", b"A")
    before = _identity(f)
    store = _store_in(tmp_path)
    result = store.trash([str(f)])

    assert sd.recover_folder(store, result.folder_name) == 0
    assert _identity(f) == before
    assert "times verified" in capsys.readouterr().out


# -- across drives: copied, and recovery says so -----------------------------


def test_across_drives_the_folder_is_copied_and_recovery_says_so(
        tmp_path, capsys, monkeypatch):
    root, files, folders = _tree(tmp_path)
    store = _store_in(tmp_path)
    result = store.trash([str(root)])
    _rename_fails_in(monkeypatch, tmp_path / "trash",
                     OSError(errno.EXDEV, "Invalid cross-device link"))

    rc = sd.recover_folder(store, result.folder_name)
    out = capsys.readouterr().out

    assert rc == 0
    assert [p.read_bytes() for p in files] == [b"1", b"22", b"333"]
    assert "copied back from another drive" in out
    assert "moved back whole" not in out
    assert not os.path.isdir(result.folder_path)        # the stored copy is gone after the copy


def test_a_rename_that_fails_for_another_reason_copies_nothing(
        tmp_path, capsys, monkeypatch):
    root, files, folders = _tree(tmp_path)
    store = _store_in(tmp_path)
    result = store.trash([str(root)])
    _rename_fails_in(monkeypatch, tmp_path / "trash",
                     PermissionError(errno.EACCES, "in use"))

    rc = sd.recover_folder(store, result.folder_name)

    assert rc == 1
    assert not root.exists()                             # nothing copied, nothing half-made
    stored = os.path.join(result.folder_path, "content", "tree", "sub", "two.txt")
    assert open(stored, "rb").read() == b"22"            # still safe in the trash
    assert "ERROR recovering" in capsys.readouterr().err


# -- what recovery reports ---------------------------------------------------


def test_a_parent_folder_that_had_to_be_created_is_mentioned(tmp_path, capsys):
    f = _write(tmp_path / "src" / "gone-later" / "a.txt", b"A")
    store = _store_in(tmp_path)
    result = store.trash([str(f)])
    os.rmdir(f.parent)                                   # the original parent disappears

    assert sd.recover_folder(store, result.folder_name) == 0
    out = capsys.readouterr().out
    assert f.read_bytes() == b"A"
    assert "no longer existed and was created" in out and str(f.parent) in out


def test_no_such_note_when_the_parent_was_still_there(tmp_path, capsys):
    f = _write(tmp_path / "src" / "a.txt", b"A")
    store = _store_in(tmp_path)
    result = store.trash([str(f)])

    assert sd.recover_folder(store, result.folder_name) == 0
    assert "no longer existed" not in capsys.readouterr().out


def test_metadata_that_cannot_be_applied_is_reported_not_swallowed(
        tmp_path, capsys, monkeypatch):
    f = _write(tmp_path / "src" / "a.txt", b"A")
    store = _store_in(tmp_path)
    result = store.trash([str(f)])

    def boom(path, metadata):
        raise RuntimeError("no such privilege")
    monkeypatch.setattr(_recover, "apply_file_metadata", boom)
    # metadata is re-applied only after a copy, so make this a cross-drive recovery
    _rename_fails_in(monkeypatch, tmp_path / "trash",
                     OSError(errno.EXDEV, "Invalid cross-device link"))

    rc = sd.recover_folder(store, result.folder_name)
    err = capsys.readouterr().err

    assert rc == 0 and f.read_bytes() == b"A"            # the content is back regardless
    assert "metadata could not be applied" in err and "no such privilege" in err


def test_metadata_is_not_rewritten_after_a_same_drive_move(tmp_path, monkeypatch):
    # A rename already brings everything back exactly; re-applying the
    # recorded values on top lost sub-millisecond precision of the creation
    # time (found by test_a_recovered_file_is_the_same_file, 2026-11-18).
    f = _write(tmp_path / "src" / "a.txt", b"A")
    store = _store_in(tmp_path)
    result = store.trash([str(f)])

    def boom(path, metadata):
        raise AssertionError("metadata was re-applied after a rename")
    monkeypatch.setattr(_recover, "apply_file_metadata", boom)

    assert sd.recover_folder(store, result.folder_name) == 0


def test_times_that_do_not_match_the_record_are_a_warning(tmp_path, capsys):
    f = _write(tmp_path / "src" / "a.txt", b"A")
    store = _store_in(tmp_path)
    result = store.trash([str(f)])
    manifest = os.path.join(result.folder_path, "manifest.json")
    with open(manifest, encoding="utf-8") as fh:
        m = json.load(fh)
    m["entries"][0]["stat"]["st_mtime"] -= 86400         # the record says a day earlier
    m["entries"][0]["metadata"] = None                   # and nothing re-applies it
    with open(manifest, "w", encoding="utf-8") as fh:
        json.dump(m, fh)

    assert sd.recover_folder(store, result.folder_name) == 0
    out = capsys.readouterr()
    assert "times differ from the record" in out.out
    assert "times verified" not in out.out
