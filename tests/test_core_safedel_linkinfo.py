"""safedel's own link detection (on dazzle-filekit) against the host engine's.

``core.safedel._linkinfo.detect_link`` replaced the import of
``dazzlecmd_lib.core.links.detect_link`` in 0.10.45. For every kind of path
safedel can be handed, the two must give the same answer on the fields the
classifier reads -- so the change alters nothing a delete records.

Everything is built under ``tmp_path``; nothing is deleted except scratch
targets the tests create themselves.
"""
import os
import sys

import pytest

from dazzlecmd_lib.core.links import detect_link as host_detect
from dazzlecmd_lib.core.safedel import _linkinfo

WINDOWS = sys.platform == "win32"
FIELDS = ("link_type", "target", "broken", "link_count", "is_dir", "inode", "size")


def _symlink_or_skip(target, link, is_dir=False):
    try:
        os.symlink(str(target), str(link), target_is_directory=is_dir)
    except (OSError, NotImplementedError):
        pytest.skip("creating a symlink needs a privilege this account lacks")


def _junction(target, link):
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


def _same(path):
    ours, theirs = _linkinfo.detect_link(str(path)), host_detect(str(path))
    if theirs is None:
        assert ours is None, ours
        return None
    assert ours is not None, "the host sees %r, safedel sees no link" % theirs.link_type
    for field in FIELDS:
        assert getattr(ours, field) == getattr(theirs, field), (
            field, getattr(ours, field), getattr(theirs, field))
    return ours


def test_a_plain_file_and_a_plain_folder_are_not_links(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    d = tmp_path / "d"
    d.mkdir()
    assert _same(f) is None
    assert _same(d) is None
    assert _same(tmp_path / "missing") is None


def test_a_file_symlink(tmp_path):
    target = tmp_path / "t.txt"
    target.write_text("x")
    link = tmp_path / "l.txt"
    _symlink_or_skip(target, link)
    info = _same(link)
    assert info.link_type == "symlink" and not info.broken and not info.is_dir


def test_a_directory_symlink(tmp_path):
    target = tmp_path / "t"
    target.mkdir()
    link = tmp_path / "l"
    _symlink_or_skip(target, link, is_dir=True)
    info = _same(link)
    assert info.link_type == "symlink" and info.is_dir and not info.broken


def test_a_broken_symlink(tmp_path):
    link = tmp_path / "l.txt"
    _symlink_or_skip(tmp_path / "never", link)
    info = _same(link)
    assert info.link_type == "symlink" and info.broken


@pytest.mark.skipif(not WINDOWS, reason="junctions are Windows-only")
def test_a_junction(tmp_path):
    target = tmp_path / "t"
    target.mkdir()
    link = tmp_path / "j"
    _junction(target, link)
    info = _same(link)
    assert info.link_type == "junction" and info.is_dir and not info.broken
    assert info.target.rstrip("\\").lower() == str(target).lower()
    assert not info.target.startswith("\\\\?\\")


@pytest.mark.skipif(not WINDOWS, reason="junctions are Windows-only")
def test_a_junction_whose_target_is_gone(tmp_path):
    target = tmp_path / "t"
    target.mkdir()
    link = tmp_path / "j"
    _junction(target, link)
    os.rmdir(target)
    info = _same(link)
    assert info.link_type == "junction" and info.broken and info.is_dir


def test_a_hardlinked_file(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("x")
    b = tmp_path / "b.txt"
    try:
        os.link(str(a), str(b))
    except OSError:
        pytest.skip("this filesystem refuses hard links")
    info = _same(a)
    assert info.link_type == "hardlink" and info.link_count == 2
    assert os.path.basename(info.target) == "b.txt"            # the other name, from the host


@pytest.mark.parametrize("name, kind", [
    ("x.lnk", "shortcut"), ("x.url", "urlshortcut"), ("x.dazzlelink", "dazzlelink"),
    ("X.URL", "urlshortcut"),
])
def test_descriptor_files_are_told_apart_by_extension(tmp_path, name, kind):
    f = tmp_path / name
    f.write_text("[InternetShortcut]\nURL=https://example.invalid/\n")
    info = _same(f)
    assert info.link_type == kind


def test_without_the_host_engine_the_answer_is_the_same_minus_the_detail(tmp_path, monkeypatch):
    """safedel on its own (no ``dazzlecmd_lib.core.links``): OS links are
    detected exactly as before; a descriptor's inner target and a hardlink's
    other name are simply not known."""
    monkeypatch.setattr(_linkinfo, "_host_detail", lambda path: None)

    url = tmp_path / "x.url"
    url.write_text("[InternetShortcut]\nURL=https://example.invalid/\n")
    info = _linkinfo.detect_link(str(url))
    assert info.link_type == "urlshortcut" and info.target is None and not info.broken

    a = tmp_path / "a.txt"
    a.write_text("x")
    try:
        os.link(str(a), str(tmp_path / "b.txt"))
    except OSError:
        pytest.skip("this filesystem refuses hard links")
    info = _linkinfo.detect_link(str(a))
    assert info.link_type == "hardlink" and info.link_count == 2 and info.target is None

    if WINDOWS:
        target = tmp_path / "t"
        target.mkdir()
        _junction(target, tmp_path / "j")
        info = _linkinfo.detect_link(str(tmp_path / "j"))
        assert info.link_type == "junction" and info.target.rstrip("\\").lower() == str(target).lower()


def test_safedel_no_longer_imports_the_host_link_engine_at_load():
    """The safety code must not need the package that hosts it. The one
    remaining reference is the optional, lazy lookup in ``_linkinfo``."""
    import pathlib
    import re
    pkg = pathlib.Path(_linkinfo.__file__).parent
    offenders = []
    for py in sorted(pkg.glob("*.py")):
        for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"^(from|import)\s+dazzlecmd_lib\.core\.links\b", line):
                offenders.append("%s:%d" % (py.name, n))
    assert offenders == []
