"""The real shape: a folder that looks like a git repository, deleted with
copy-first and recovered -- the sequence a 2026-11-19 live run broke on.

What went wrong then (dazzlecmd-lib 0.10.49): three repository copies were
staged with copy-first into one batch and recovered. Recovery then removed the
spent batch with a plain rmtree, which stopped at the first read-only file (a
git object) after deleting everything that sorted before it: one copy-first
was left half-deleted, the batch stayed listed as recoverable, and
`--contains <one>` had recovered all three items. Directory sizes in `list`
showed 4-8 KB for folders of megabytes.

These tests build that shape under ``tmp_path``: many files, a nested
``.git`` with READ-ONLY object files, several items in one batch.
"""
import json
import os
import stat
import sys

import pytest

import dazzlecmd_lib.core.safedel as sd
from dazzlecmd_lib.core.safedel import _recover


def _repo_like(root, name, files=40):
    """A folder shaped like a repository copy: sources, a nested private/.git
    whose objects are read-only (as git leaves them), and a __pycache__."""
    top = root / name
    (top / "pkg" / "__pycache__").mkdir(parents=True)
    (top / "private" / ".git" / "objects" / "ab").mkdir(parents=True)
    (top / "docs").mkdir()
    for i in range(files):
        (top / "pkg" / ("m%02d.py" % i)).write_text("x = %d\n" % i)
    (top / "pkg" / "__pycache__" / "m00.cpython-312.pyc").write_bytes(b"\0" * 64)
    (top / "README.md").write_text("readme")
    (top / "docs" / "index.md").write_text("docs")
    for i in range(5):
        obj = top / "private" / ".git" / "objects" / "ab" / ("%038d" % i)
        obj.write_bytes(b"object %d" % i)
        os.chmod(obj, stat.S_IREAD)                      # read-only, like git
    return top


def _count(path):
    return sum(len(fs) for _d, _ds, fs in os.walk(path))


def _store(tmp_path):
    return sd.TrashStore(store_path=str(tmp_path / "trash"),
                         registry_path=str(tmp_path / "reg.json"))


def _make_writable(path):
    for d, _ds, fs in os.walk(path):
        for f in fs:
            try:
                os.chmod(os.path.join(d, f), stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass


@pytest.fixture(autouse=True)
def _writable_after(tmp_path):
    yield
    _make_writable(tmp_path)          # so pytest can clean its own temp folder


def test_a_repo_copy_round_trips_and_the_spent_batch_is_gone(tmp_path, capsys):
    src = tmp_path / "work"
    src.mkdir()
    a = _repo_like(src, "a")
    n = _count(a)
    store = _store(tmp_path)

    result = store.trash([str(a)], copy_first=True, copy_first_max=10 ** 9)
    assert result.success, result.errors
    batch = os.path.join(str(tmp_path / "trash"), result.folder_name)
    assert _count(os.path.join(batch, "copy-first")) == n         # the extra copy is whole

    rc = sd.recover_folder(store, result.folder_name)
    out = capsys.readouterr().out

    assert rc == 0, out
    assert _count(a) == n                                          # everything came back
    assert not os.path.exists(batch)                               # the spent batch is gone ...
    assert not [x for x in os.listdir(str(tmp_path / "trash"))
                if x.startswith(store.REMOVING_PREFIX)]            # ... with nothing left over
    assert "Kept trash folder" not in out


def test_a_removal_that_fails_leaves_no_half_batch_behind(tmp_path, monkeypatch, capsys):
    """If the spent batch cannot be fully removed, it is renamed out of the
    batch namespace first, so `list` and `recover` stop offering it."""
    src = tmp_path / "work"
    src.mkdir()
    a = _repo_like(src, "a")
    store = _store(tmp_path)
    result = store.trash([str(a)], copy_first=True, copy_first_max=10 ** 9)
    assert result.success

    import shutil

    def boom(path, *a, **k):
        raise OSError("simulated: a file is in use")
    monkeypatch.setattr(shutil, "rmtree", boom)

    rc = sd.recover_folder(store, result.folder_name)
    out = capsys.readouterr().out

    assert rc == 1                                                  # kept is reported
    assert "no longer listed as a batch" in out
    names = os.listdir(str(tmp_path / "trash"))
    assert result.folder_name not in names
    assert any(x.startswith(store.REMOVING_PREFIX) for x in names)
    assert store.list_entries() == [] or all(
        f.folder_name != result.folder_name for f in store.list_entries())


def test_a_batch_in_use_is_not_touched_at_all(tmp_path, monkeypatch):
    """When even the rename fails (the folder is in use), nothing is deleted."""
    src = tmp_path / "work"
    src.mkdir()
    a = _repo_like(src, "a")
    store = _store(tmp_path)
    result = store.trash([str(a)], copy_first=True, copy_first_max=10 ** 9)
    batch = os.path.join(str(tmp_path / "trash"), result.folder_name)
    before = _count(batch)

    from dazzlecmd_lib.core.safedel import _store as store_mod
    real = os.rename

    def fake(s, d, *a, **k):
        if os.path.basename(str(d)).startswith(store.REMOVING_PREFIX):
            raise PermissionError("in use")
        return real(s, d, *a, **k)
    monkeypatch.setattr(store_mod.os, "rename", fake)

    assert store.remove_folder(result.folder_name, batch) is False
    assert _count(batch) == before
    assert store.last_remove_leftover is None


def test_contains_recovers_only_the_named_item_from_the_named_batch(tmp_path, capsys):
    src = tmp_path / "work"
    src.mkdir()
    a, b, c = (_repo_like(src, n, files=5) for n in ("alpha", "beta", "gamma"))
    store = _store(tmp_path)
    result = store.trash([str(a), str(b), str(c)])
    assert result.success
    elsewhere = src / "elsewhere"
    elsewhere.mkdir()
    other_gamma = _repo_like(elsewhere, "gamma", files=2)          # same name, a second batch
    other = store.trash([str(other_gamma)])
    assert other.success

    rc = sd.cmd_recover(store, [result.folder_name], contains="gamma")
    out = capsys.readouterr().out

    assert rc == 0, out
    assert os.path.isdir(c)                                        # the one asked for
    assert not os.path.exists(a) and not os.path.exists(b)         # the others stay in the trash
    assert not os.path.exists(other_gamma)                         # the other batch's gamma stays put
    assert "2 other item(s) not selected" in out

    folder = next(f for f in store.list_entries() if f.folder_name == result.folder_name)
    marked = {os.path.basename(e.original_path): e.recovered_at for e in folder.entries}
    assert marked["gamma"] and not marked["alpha"] and not marked["beta"]

    rc = sd.cmd_recover(store, [result.folder_name])               # the rest, later
    out = capsys.readouterr().out
    assert rc == 0, out
    assert os.path.isdir(a) and os.path.isdir(b)
    assert "already recovered" in out                              # gamma is not tried again
    assert not os.path.exists(os.path.join(str(tmp_path / "trash"), result.folder_name))


def test_list_shows_a_folders_contents_size_not_its_entry_size(tmp_path, capsys):
    src = tmp_path / "work"
    src.mkdir()
    big = src / "big"
    big.mkdir()
    (big / "blob.bin").write_bytes(b"\1" * 300_000)
    store = _store(tmp_path)
    result = store.trash([str(big)])
    assert result.success
    capsys.readouterr()

    sd.cmd_list(store, [result.folder_name])
    out = capsys.readouterr().out

    assert "(293.0 KB)" in out, out                                 # 300,000 bytes, not the 4-8 KB entry
