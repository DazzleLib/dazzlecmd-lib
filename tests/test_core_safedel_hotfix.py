"""Hotfix tests for DazzleLib/dazzlecmd-lib#7.

1. Staging never overwrites: two items with the same name in one batch used
   to land on one content/<name> path, and the later one replaced the earlier
   (Windows: rename FileExistsError fell through to shutil.copy2; POSIX:
   os.rename replaces silently). Now the second item is refused and left in
   place, and the copy fallback runs only for a cross-device rename (EXDEV).
2. Paths are taken literally: classify("~") used to resolve to the home
   folder because filekit's normalizer expands ~ and environment variables.

Safety (global rule 1b): every test works under tmp_path with an explicit
store_path AND registry_path (no per-volume routing, nothing under ~/.safedel).
The literal-"~" trash test wraps every path-touching step in a guard that
raises if handed the real home folder, so a reverted fix fails fast instead of
walking, staging or deleting the home folder.
"""
import errno
import os

import pytest

import dazzlecmd_lib.core.safedel as sd
from dazzlecmd_lib.core.safedel import _platform, _store


def _store_in(tmp_path):
    return sd.TrashStore(store_path=str(tmp_path / "trash"),
                         registry_path=str(tmp_path / "reg.json"))


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# -- 1. staging never overwrites -------------------------------------------


def test_same_name_files_lose_no_bytes(tmp_path):
    """Three same-named files: one is staged, two stay in place, and every
    original's bytes still exist somewhere (in place or in the trash)."""
    originals = {}
    for sub in ("a", "b", "c"):
        p = tmp_path / "src" / sub / "x.json"
        data = f"content-{sub}".encode() * (len(originals) + 1)
        _write(p, data)
        originals[str(p)] = data

    result = _store_in(tmp_path).trash(list(originals))

    assert not result.success
    assert len(result.errors) == 2
    staged = [e for e in result.entries if e.content_path]
    assert len(staged) == 1
    for path, data in originals.items():
        entry = next(e for e in result.entries if e.original_path == path)
        if entry.content_path:
            got = open(os.path.join(result.folder_path, entry.content_path), "rb").read()
            assert not os.path.exists(path)
        else:
            got = open(path, "rb").read()   # refused: still in place
        assert got == data, path


def test_same_name_dirs_second_left_in_place(tmp_path):
    a = tmp_path / "src" / "a" / "d"
    b = tmp_path / "src" / "b" / "d"
    _write(a / "ia.txt", b"A")
    _write(b / "ib.txt", b"B")

    result = _store_in(tmp_path).trash([str(a), str(b)])

    assert not result.success
    assert not a.exists()
    assert (b / "ib.txt").read_bytes() == b"B"


def _classified(path):
    return sd.classify(str(path))


def test_preexisting_destination_refused_for_file(tmp_path):
    src = tmp_path / "src" / "x.txt"
    _write(src, b"new")
    dest_dir = tmp_path / "content"
    _write(dest_dir / "x.txt", b"already staged")

    sr = sd.stage_to_trash(str(src), str(dest_dir), _classified(src))

    assert not sr.success
    assert sr.method == "refused"
    assert (dest_dir / "x.txt").read_bytes() == b"already staged"
    assert src.read_bytes() == b"new"


def test_preexisting_destination_refused_for_dir(tmp_path):
    src = tmp_path / "src" / "d"
    _write(src / "inner.txt", b"new")
    dest_dir = tmp_path / "content"
    _write(dest_dir / "d" / "other.txt", b"already staged")

    sr = sd.stage_to_trash(str(src), str(dest_dir), _classified(src))

    assert not sr.success
    assert (dest_dir / "d" / "other.txt").read_bytes() == b"already staged"
    assert not (dest_dir / "d" / "inner.txt").exists()
    assert (src / "inner.txt").read_bytes() == b"new"


def test_preexisting_destination_refused_when_it_is_a_symlink(tmp_path):
    src = tmp_path / "src" / "x.txt"
    _write(src, b"new")
    dest_dir = tmp_path / "content"
    dest_dir.mkdir()
    target = tmp_path / "elsewhere.txt"
    _write(target, b"link target")
    try:
        os.symlink(str(target), str(dest_dir / "x.txt"))
    except (OSError, NotImplementedError):
        pytest.skip("cannot create symlinks here")

    sr = sd.stage_to_trash(str(src), str(dest_dir), _classified(src))

    assert not sr.success
    assert target.read_bytes() == b"link target"
    assert src.read_bytes() == b"new"


def test_posix_style_rename_cannot_replace_a_staged_item(tmp_path, monkeypatch):
    """On POSIX, os.rename onto an existing file replaces it silently, so the
    lexists refusal is the only thing protecting an already-staged item.
    Simulate that on any platform by making rename overwrite (os.replace)."""
    first = tmp_path / "src" / "a" / "x.json"
    second = tmp_path / "src" / "b" / "x.json"
    _write(first, b"first item")
    _write(second, b"second item")
    dest_dir = tmp_path / "content"

    monkeypatch.setattr(_platform.os, "rename", os.replace)

    sr1 = sd.stage_to_trash(str(first), str(dest_dir), _classified(first))
    sr2 = sd.stage_to_trash(str(second), str(dest_dir), _classified(second))

    assert sr1.success
    assert sr1.source_path == str(first)
    assert sr1.dest_path == str(dest_dir / "x.json")
    assert not sr2.success
    assert (dest_dir / "x.json").read_bytes() == b"first item"
    assert second.read_bytes() == b"second item"


def test_staged_broken_symlink_still_counts_as_taken(tmp_path, monkeypatch):
    """Staging a broken symlink recreates it (dangling) in the trash. A later
    same-named item must still be refused, not renamed over it."""
    link_dir = tmp_path / "src" / "a"
    link_dir.mkdir(parents=True)
    broken = link_dir / "x.json"
    try:
        os.symlink(str(tmp_path / "no-such-target"), str(broken))
    except (OSError, NotImplementedError):
        pytest.skip("cannot create symlinks here")
    regular = tmp_path / "src" / "b" / "x.json"
    _write(regular, b"regular item")
    dest_dir = tmp_path / "content"

    sr1 = sd.stage_to_trash(str(broken), str(dest_dir), _classified(broken))
    if not os.path.lexists(dest_dir / "x.json"):
        pytest.skip("broken link was not recreated in the trash here")
    monkeypatch.setattr(_platform.os, "rename", os.replace)   # POSIX semantics
    sr2 = sd.stage_to_trash(str(regular), str(dest_dir), _classified(regular))

    assert sr1.success
    assert not sr2.success
    assert os.path.islink(dest_dir / "x.json")
    assert regular.read_bytes() == b"regular item"


def test_posix_style_dir_rename_cannot_replace_a_staged_empty_dir(tmp_path, monkeypatch):
    """On POSIX, renaming a directory onto an EMPTY directory replaces it.
    The refusal must cover directories, not just files."""
    first = tmp_path / "src" / "a" / "d"
    second = tmp_path / "src" / "b" / "d"
    first.mkdir(parents=True)                    # an empty folder is an item too
    _write(second / "inner.txt", b"second item")
    dest_dir = tmp_path / "content"

    def posix_rename(src, dst):
        if os.path.isdir(src) and os.path.isdir(dst) and not os.listdir(dst):
            os.rmdir(dst)
        os.replace(src, dst)

    monkeypatch.setattr(_platform.os, "rename", posix_rename)

    sr1 = sd.stage_to_trash(str(first), str(dest_dir), _classified(first))
    sr2 = sd.stage_to_trash(str(second), str(dest_dir), _classified(second))

    assert sr1.success
    assert not sr2.success
    assert os.listdir(dest_dir / "d") == []      # the staged empty folder is untouched
    assert (second / "inner.txt").read_bytes() == b"second item"


@pytest.mark.parametrize("err", [errno.EACCES, errno.EEXIST, errno.ENOTEMPTY])
def test_non_exdev_rename_failure_refuses_without_copying(tmp_path, monkeypatch, err):
    """EEXIST is the Windows errno of the original bug (rename onto a taken
    name); it must refuse, never fall through to a copy."""
    src = tmp_path / "src" / "x.txt"
    _write(src, b"data")
    dest_dir = tmp_path / "content"
    dest_dir.mkdir()

    def fail_rename(a, b):
        raise OSError(err, os.strerror(err), a)

    def no_copy(*a, **k):
        raise AssertionError("copy fallback must not run for a non-EXDEV error")

    monkeypatch.setattr(_platform.os, "rename", fail_rename)
    monkeypatch.setattr(_platform.shutil, "copy2", no_copy)

    sr = sd.stage_to_trash(str(src), str(dest_dir), _classified(src))

    assert not sr.success
    assert not (dest_dir / "x.txt").exists()
    assert src.read_bytes() == b"data"


def test_exdev_rename_still_falls_back_to_copy(tmp_path, monkeypatch):
    src = tmp_path / "src" / "x.txt"
    _write(src, b"data")
    dest_dir = tmp_path / "content"
    dest_dir.mkdir()

    def cross_device(a, b):
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(_platform.os, "rename", cross_device)

    sr = sd.stage_to_trash(str(src), str(dest_dir), _classified(src))

    assert sr.success
    assert sr.method == "copy"
    assert (dest_dir / "x.txt").read_bytes() == b"data"


def test_partial_cross_device_copy_is_removed(tmp_path, monkeypatch):
    src = tmp_path / "src" / "d"
    _write(src / "one.txt", b"1")
    _write(src / "two.txt", b"2")
    dest_dir = tmp_path / "content"
    dest_dir.mkdir()

    def cross_device(a, b):
        raise OSError(errno.EXDEV, "cross-device link")

    def half_copy(source, dest):
        os.makedirs(dest)
        with open(os.path.join(dest, "one.txt"), "wb") as f:
            f.write(b"1")
        raise OSError(errno.ENOSPC, "disk full")

    import dazzle_filekit.operations as fk_ops
    monkeypatch.setattr(_platform.os, "rename", cross_device)
    monkeypatch.setattr(fk_ops, "copy_tree_preserving_links", half_copy)

    sr = sd.stage_to_trash(str(src), str(dest_dir), _classified(src))

    assert not sr.success
    assert not (dest_dir / "d").exists()          # no partial tree left behind
    assert (src / "two.txt").read_bytes() == b"2"  # original untouched
    assert all(isinstance(w, str) for w in sr.warnings)


def test_partial_copy_cleanup_failure_is_reported(tmp_path, monkeypatch):
    """If the partial copy cannot be removed, the result says where it is."""
    src = tmp_path / "src" / "d"
    _write(src / "one.txt", b"1")
    dest_dir = tmp_path / "content"
    dest_dir.mkdir()

    def cross_device(a, b):
        raise OSError(errno.EXDEV, "cross-device link")

    def half_copy(source, dest):
        os.makedirs(dest)
        raise OSError(errno.ENOSPC, "disk full")

    def stuck_rmtree(path, *a, **k):
        raise PermissionError(errno.EACCES, "in use", path)

    import dazzle_filekit.operations as fk_ops
    monkeypatch.setattr(_platform.os, "rename", cross_device)
    monkeypatch.setattr(fk_ops, "copy_tree_preserving_links", half_copy)
    monkeypatch.setattr(_platform.shutil, "rmtree", stuck_rmtree)

    sr = sd.stage_to_trash(str(src), str(dest_dir), _classified(src))

    assert not sr.success
    assert any("Partial copy left in trash" in w and str(dest_dir / "d") in w
               for w in sr.warnings), sr.warnings


# -- 2. paths are taken literally -------------------------------------------


@pytest.mark.parametrize("name", ["~", "$HOME", "%USERPROFILE%"])
def test_classify_takes_expandable_names_literally(tmp_path, monkeypatch, name):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "fake-home"))
    try:
        (tmp_path / name).mkdir()
    except OSError:
        pytest.skip(f"cannot create a folder named {name!r} here")

    c = sd.classify(name)

    assert os.path.normcase(c.path) == os.path.normcase(str(tmp_path / name))
    assert c.exists


def test_classify_literal_path_is_normalized(tmp_path, monkeypatch):
    """The literal branch still collapses '.' segments and separators."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "fake-home"))
    (tmp_path / "~" / "sub").mkdir(parents=True)

    c = sd.classify("~/./sub")

    assert c.path == os.path.normpath(str(tmp_path / "~" / "sub"))
    assert c.exists


def test_classify_keeps_surrounding_whitespace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    try:
        (tmp_path / " lead").write_text("x")
    except OSError:
        pytest.skip("cannot create a name with a leading space here")

    c = sd.classify(" lead")

    assert c.path.endswith(os.sep + " lead")
    assert c.exists


def test_plain_paths_still_use_filekit_normalization(tmp_path):
    f = tmp_path / "plain.txt"
    f.write_text("x")
    c = sd.classify(str(f).replace(os.sep, "/"))
    assert os.path.normcase(c.path) == os.path.normcase(str(f))


def test_trash_of_literal_tilde_never_reaches_home(tmp_path, monkeypatch):
    """`trash(["~"])` stages the literal ./~ folder. Every path-touching step
    is guarded so a regression raises instead of touching the home folder."""
    real_home = os.path.normcase(os.path.abspath(os.path.expanduser("~")))

    def guard(fn):
        def wrapped(*args, **kwargs):
            for a in args:
                items = a if isinstance(a, (list, tuple)) else [a]
                for item in items:
                    if isinstance(item, str) and \
                            os.path.normcase(os.path.abspath(item)) == real_home:
                        raise AssertionError(f"{fn.__name__} was handed the home folder")
            return fn(*args, **kwargs)
        wrapped.__name__ = fn.__name__
        return wrapped

    monkeypatch.setattr(_store, "calculate_size", guard(_store.calculate_size))
    monkeypatch.setattr(_store, "stage_to_trash", guard(_store.stage_to_trash))
    monkeypatch.setattr(_store, "safe_delete", guard(_store.safe_delete))
    monkeypatch.setattr(_store, "collect_file_metadata",
                        guard(_store.collect_file_metadata))

    work = tmp_path / "work"
    (work / "~").mkdir(parents=True)
    (work / "~" / "junk.txt").write_text("literal tilde folder")
    monkeypatch.chdir(work)

    result = _store_in(tmp_path).trash(["~"])

    assert result.success, result.errors
    assert not (work / "~").exists()
    staged = os.path.join(result.folder_path, result.entries[0].content_path)
    assert os.path.isfile(os.path.join(staged, "junk.txt"))
