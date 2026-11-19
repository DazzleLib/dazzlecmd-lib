"""Links whose target is gone: can safedel still delete and recover them?

Two suspicions raised by a read-only review on 2026-11-18, written here as
tests before anything is fixed:

1. a broken symlink (its target does not exist) may not be recoverable;
2. a junction whose target folder is gone may not be recognised as a junction.

A link with a missing target is still a real thing a person may delete by
mistake, and "recoverable" has to hold for it too.

Everything is under ``tmp_path``. The only things removed are the links
themselves and the scratch target folders the tests create.
"""
import os
import sys

import pytest

import dazzlecmd_lib.core.safedel as sd

WINDOWS = sys.platform == "win32"


def _store_in(tmp_path):
    return sd.TrashStore(store_path=str(tmp_path / "trash"),
                         registry_path=str(tmp_path / "reg.json"))


def _symlink_or_skip(target, link, is_dir=False):
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(str(target), str(link), target_is_directory=is_dir)
    except (OSError, NotImplementedError):
        pytest.skip("creating a symlink needs a privilege this account lacks")


def _is_link(path):
    if os.path.islink(path):
        return True
    if WINDOWS and os.path.lexists(path):
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & 0x400)
    return False


def test_a_broken_file_symlink_is_deleted_and_recovered(tmp_path, capsys):
    link = tmp_path / "src" / "dangling.txt"
    missing = tmp_path / "never-existed.txt"
    _symlink_or_skip(missing, link)
    assert os.path.lexists(link) and not os.path.exists(link)      # a link to nothing
    store = _store_in(tmp_path)

    result = store.trash([str(link)])
    assert result.success, (result.errors, result.postcondition_failures)
    assert not os.path.lexists(link)

    rc = sd.recover_folder(store, result.folder_name)
    out = capsys.readouterr()

    assert rc == 0, out.out + out.err
    assert os.path.islink(link)                                    # the link is back ...
    assert os.readlink(link).rstrip("\\/").endswith("never-existed.txt")   # ... pointing where it did


@pytest.mark.skipif(not WINDOWS, reason="junctions are Windows-only")
def test_a_junction_whose_target_is_gone_is_still_treated_as_a_link(tmp_path, capsys):
    import _winapi
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "src" / "jn"
    link.parent.mkdir(parents=True)
    _winapi.CreateJunction(str(target), str(link))
    os.rmdir(target)                                               # the target disappears
    assert os.path.lexists(link) and not os.path.exists(link)

    c = sd.classify(str(link))
    assert c.exists
    assert c.file_type.value in ("junction", "broken_link"), c.file_type   # never an ordinary folder

    store = _store_in(tmp_path)
    result = store.trash([str(link)])
    assert result.success, (result.errors, result.postcondition_failures)
    assert not os.path.lexists(link)

    rc = sd.recover_folder(store, result.folder_name)
    out = capsys.readouterr()

    assert rc == 0, out.out + out.err
    assert _is_link(link)                                          # a link again, not a folder
    assert os.path.isjunction(link) if hasattr(os.path, "isjunction") else True
    back = os.readlink(link)
    assert back.rstrip("\\/").lower().endswith("target"), back     # ... pointing where it did
    assert "target does not exist" in out.out                      # and the person is told
