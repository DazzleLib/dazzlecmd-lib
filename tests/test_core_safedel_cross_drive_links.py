"""A folder that holds a link, staged ACROSS drives: the link's target is safe.

On one drive, staging is a rename and a link inside a folder travels as a
link. When the trash is on another drive the folder must be copied and the
original removed, and that is where a link could go wrong twice:

1. the copy could ENTER the link and copy what it points at into the trash
   (it did, through dazzle-filekit 0.4.3: measured 2026-11-18);
2. removing the original could delete THROUGH the link.

These tests pin both, for a junction (Windows) or a symlink (elsewhere), and
then the way back: recovering such a batch across drives recreates the link
and still does not touch the target.

A second drive cannot be built in a temp folder, so "across drives" is
simulated the way the staging code experiences it: ``os.rename`` raises
``EXDEV`` for the item, and the engine takes its copy path. Everything else
is real, on real files.

Rule 1b: the link's target is a scratch folder under ``tmp_path``. If either
property failed, the damage would be to that scratch folder. Nothing outside
``tmp_path`` is named.
"""
import errno
import os
import sys

import pytest

import dazzlecmd_lib.core.safedel as sd
from dazzlecmd_lib.core.safedel import _platform, _recover

WINDOWS = sys.platform == "win32"


def _store_in(tmp_path):
    return sd.TrashStore(store_path=str(tmp_path / "trash"),
                         registry_path=str(tmp_path / "reg.json"))


def _write(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _dir_link(target, link):
    link.parent.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(str(target), str(link), target_is_directory=True)


def _is_link(path):
    if os.path.islink(path):
        return True
    if WINDOWS:
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & 0x400)
    return False


def _cross_device(monkeypatch, module, under):
    """Make ``module``'s os.rename raise EXDEV for sources under ``under``
    going outside it. A rename that stays under ``under`` is on one drive and
    succeeds (0.10.50 renames a spent batch inside its store before removal)."""
    real = os.rename
    prefix = os.path.normcase(str(under)) + os.sep

    def fake(src, dst, *a, **k):
        s = os.path.normcase(str(src))
        d = os.path.normcase(str(dst))
        if (s.startswith(prefix) or s == prefix.rstrip(os.sep)) and not d.startswith(prefix):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(module.os, "rename", fake)


@pytest.fixture
def shape(tmp_path):
    """work/ holds a file and a link to precious/, which holds two files."""
    precious = tmp_path / "precious"
    _write(precious / "notes.md", b"notes")
    _write(precious / "sub" / "more.md", b"more")
    work = tmp_path / "src" / "work"
    _write(work / "code.py", b"code")
    _dir_link(precious, work / "private")
    return work, precious


def _precious_intact(precious):
    return ((precious / "notes.md").read_bytes() == b"notes"
            and (precious / "sub" / "more.md").read_bytes() == b"more"
            and sorted(os.listdir(precious)) == ["notes.md", "sub"])


def test_the_copy_keeps_the_link_and_the_removal_spares_its_target(
        tmp_path, monkeypatch, shape):
    work, precious = shape
    _cross_device(monkeypatch, _platform, tmp_path / "src")

    result = _store_in(tmp_path).trash([str(work)])

    assert result.success, (result.errors, result.postcondition_failures)
    assert result.staging_results[0].method == "copy"        # the cross-drive path really ran
    assert not work.exists()                                 # the original is gone ...
    assert _precious_intact(precious)                        # ... and its link's target is whole
    staged = os.path.join(result.folder_path, "content", "work")
    assert open(os.path.join(staged, "code.py"), "rb").read() == b"code"
    link_in_trash = os.path.join(staged, "private")
    assert _is_link(link_in_trash)                           # stored as a link ...
    # ... and not as a copy of what it points at: the trash holds no second
    # notes.md of its own (the one seen through the link IS the original)
    assert os.path.samefile(os.path.join(link_in_trash, "notes.md"),
                            precious / "notes.md")


def test_recovering_it_across_drives_recreates_the_link(tmp_path, monkeypatch, shape):
    work, precious = shape
    _cross_device(monkeypatch, _platform, tmp_path / "src")
    store = _store_in(tmp_path)
    result = store.trash([str(work)])
    assert result.success
    _cross_device(monkeypatch, _recover, tmp_path / "trash")  # and back across drives too

    rc = sd.recover_folder(store, result.folder_name)

    assert rc == 0
    assert (work / "code.py").read_bytes() == b"code"
    assert _is_link(work / "private")
    assert os.path.samefile(work / "private" / "notes.md", precious / "notes.md")
    assert _precious_intact(precious)
    assert not os.path.isdir(result.folder_path)             # the stored copy was removed ...
    assert _precious_intact(precious)                        # ... without reaching through its link


def test_on_one_drive_the_link_travels_as_a_link_both_ways(tmp_path, shape):
    work, precious = shape
    store = _store_in(tmp_path)

    result = store.trash([str(work)])
    assert result.success and result.staging_results[0].method == "rename"
    assert _is_link(os.path.join(result.folder_path, "content", "work", "private"))
    assert _precious_intact(precious)

    assert sd.recover_folder(store, result.folder_name) == 0
    assert _is_link(work / "private") and _precious_intact(precious)
