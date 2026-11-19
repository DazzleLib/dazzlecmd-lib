"""The "other runtime" name of a path, recorded in a manifest as
``original_path_alt`` so a batch written on Windows can be recovered from WSL
and the reverse.

Since 0.10.43 it is computed from dazzle-filekit's portable path key instead
of a private regex. These tests pin what the manifest has always stored for
normalised paths (so the roughly 1,700 batches on disk and new ones agree),
and the cases with no other-runtime name.

Pure string work, except the last test, which deletes one file under
``tmp_path`` into a store under ``tmp_path`` and reads the manifest.
"""
import json
import os
import sys

import pytest

import dazzle_filekit.utils.compat as compat
from dazzlecmd_lib.core.safedel import _store


@pytest.fixture
def on_windows(monkeypatch):
    monkeypatch.setattr(compat, "is_windows", lambda: True)


@pytest.fixture
def on_posix(monkeypatch):
    monkeypatch.setattr(compat, "is_windows", lambda: False)


@pytest.mark.parametrize("native, alt", [
    ("C:\\Users\\x\\a.txt", "/mnt/c/Users/x/a.txt"),
    ("c:\\Users\\x", "/mnt/c/Users/x"),                 # the drive letter is lowered
    ("E:\\code\\a b\\c.txt", "/mnt/e/code/a b/c.txt"),  # spaces survive
    ("C:/Users/x", "/mnt/c/Users/x"),                   # forward slashes accepted
    ("C:\\a\\b.", "/mnt/c/a/b."),                       # a trailing dot is kept
    ("C:\\\u00e9\\x", "/mnt/c/\u00e9/x"),
])
def test_a_windows_drive_path_gets_its_wsl_name(on_windows, native, alt):
    assert _store._compute_alt_path(native) == alt


@pytest.mark.parametrize("native, alt", [
    ("/mnt/c/Users/x/a.txt", "C:\\Users\\x\\a.txt"),
    ("/mnt/C/Users/x", "C:\\Users\\x"),                 # the drive letter is raised
    ("/mnt/e/code/a b/c.txt", "E:\\code\\a b\\c.txt"),
    ("/mnt/c/", "C:\\"),
    ("/c/Users/x", "C:\\Users\\x"),                     # the MSYS form, kept as it was
])
def test_a_wsl_mount_path_gets_its_windows_name(on_posix, native, alt):
    assert _store._compute_alt_path(native) == alt


@pytest.mark.parametrize("native", [
    "\\\\server\\share\\a",      # UNC: no /mnt name
    "\\\\?\\C:\\a\\b",           # an extended-length path is not rewritten
    "C:rel",                     # drive-relative
    "relative\\path",
    "",
])
def test_a_windows_path_with_no_wsl_name_gives_none(on_windows, native):
    assert _store._compute_alt_path(native) is None


@pytest.mark.parametrize("native", [
    "/home/x/a.txt",             # a Linux path has no Windows name
    "/mnt/cc/a",                 # not a drive mount
    "/mnt",
    "relative/path",
    "",
])
def test_a_posix_path_with_no_windows_name_gives_none(on_posix, native):
    assert _store._compute_alt_path(native) is None


def test_the_two_directions_undo_each_other(monkeypatch):
    monkeypatch.setattr(compat, "is_windows", lambda: True)
    wsl = _store._compute_alt_path("D:\\proj\\src\\main.py")
    monkeypatch.setattr(compat, "is_windows", lambda: False)
    assert _store._compute_alt_path(wsl) == "D:\\proj\\src\\main.py"


def test_a_path_the_key_refuses_gives_none_instead_of_raising(on_windows, monkeypatch):
    import dazzle_filekit.portable as portable

    def refuse(path, *, flavor):
        raise portable.PortablePathError("refused for the test")

    monkeypatch.setattr(portable, "encode_portable_path", refuse)
    assert _store._compute_alt_path("C:\\Users\\x") is None


@pytest.mark.skipif(sys.platform != "win32", reason="a pytest temp path has a WSL name only on Windows")
def test_a_written_manifest_carries_the_other_name(tmp_path):
    import dazzlecmd_lib.core.safedel as sd
    victim = tmp_path / "work" / "a.txt"
    victim.parent.mkdir()
    victim.write_text("x")
    store = sd.TrashStore(store_path=str(tmp_path / "trash"),
                          registry_path=str(tmp_path / "reg.json"))

    result = store.trash([str(victim)])
    assert result.success, result.errors

    with open(os.path.join(str(tmp_path / "trash"), result.folder_name, "manifest.json"),
              encoding="utf-8") as fh:
        entry = json.load(fh)["entries"][0]
    drive, rest = os.path.splitdrive(entry["original_path"])
    assert entry["original_path_alt"] == "/mnt/%s%s" % (drive[0].lower(), rest.replace("\\", "/"))


def test_unnormalised_input_is_normalised_lexically(on_windows):
    # Not what safedel passes (it passes absolute, normalised paths); pinned so
    # a change in the key's behaviour here is noticed.
    assert _store._compute_alt_path("C:\\a\\.\\b\\") == "/mnt/c/a/b"
    assert _store._compute_alt_path("C:\\a\\\\b") == "/mnt/c/a/b"
