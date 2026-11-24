"""Reading a link's target never crashes on a reparse point that isn't a symlink
or junction (0.10.54).

os.readlink raises ValueError ("not a symbolic link") for a WSL symlink
(0xA000001D), an app execution alias or a cloud placeholder. Two sites decided
"is a link" from the reparse bit and caught only OSError. Reported by the
TEMPMESS session's read-only review on 2026-11-23, after the same fault was fixed
in safedel's copy-first (0.10.52). os.readlink is patched to raise, so no real
WSL link or cloud file is needed.
"""
import os

import pytest

import dazzlecmd_lib.core.links as links
from dazzlecmd_lib.core.links import _detect


def _readlink_raises(exc):
    def fake(path, *a, **k):
        raise exc
    return fake


@pytest.mark.parametrize("exc", [ValueError("not a symbolic link"), NotImplementedError()])
def test_get_link_target_returns_none_for_a_foreign_reparse_point(tmp_path, monkeypatch, exc):
    monkeypatch.setattr(links, "is_linked_project", lambda p: True)   # the reparse bit is set
    monkeypatch.setattr(links.os, "readlink", _readlink_raises(exc))
    assert links.get_link_target(str(tmp_path)) is None


@pytest.mark.parametrize("exc", [ValueError("not a symbolic link"), NotImplementedError()])
def test_safe_readlink_returns_none_for_a_foreign_reparse_point(tmp_path, monkeypatch, exc):
    monkeypatch.setattr(_detect.os, "readlink", _readlink_raises(exc))
    assert _detect._safe_readlink(str(tmp_path)) is None


def test_an_ordinary_oserror_still_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(links, "is_linked_project", lambda p: True)
    monkeypatch.setattr(links.os, "readlink", _readlink_raises(OSError(22, "invalid")))
    assert links.get_link_target(str(tmp_path)) is None
