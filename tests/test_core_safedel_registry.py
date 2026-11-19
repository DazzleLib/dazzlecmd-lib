"""The link registry: a record of links a tool created, so the folder a link
points at can be protected from deletion.

``register_link`` / ``unregister_link`` / ``read_registry`` keep a small JSON
file (``~/.safedel/junctions.json`` by default). ``links_pointing_at`` reads
it, together with the worktrees of any enclosing git repository, when safedel
decides whether a folder is some link's destination.

These two tests lived in ``test_core_safedel_worktree.py`` until the worktree
teardown engine was removed (0.10.41); they are unchanged.

Everything is under ``tmp_path``; the default registry under the home folder
is never read or written.
"""
import json
import os
import sys

from dazzlecmd_lib.core.safedel import _junctions

WINDOWS = sys.platform == "win32"


def _dir_link(target, link):
    """Make a directory link without a shell and without resolving anything.
    Windows: a junction. Elsewhere: a symlink. Returns the kind."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
        return "junction"
    os.symlink(str(target), str(link), target_is_directory=True)
    return "symlink"


def test_registry_round_trip(tmp_path):
    reg = str(tmp_path / "state" / "junctions.json")
    target = tmp_path / "data"
    target.mkdir()
    link = tmp_path / "view"
    kind = _dir_link(target, link)

    _junctions.register_link(str(link), str(target), kind, reg, created_by="test")
    _junctions.register_link(str(link), str(target), kind, reg)       # replaces, not duplicates

    records = _junctions.read_registry(reg)
    assert len(records) == 1 and records[0]["type"] == kind
    assert json.load(open(reg))[0]["link"] == str(link)
    hits = _junctions.links_pointing_at(str(target), reg)
    assert [(h.link_path, h.source) for h in hits] == [(str(link), "registry")]

    assert _junctions.unregister_link(str(link), reg) is True
    assert _junctions.unregister_link(str(link), reg) is False
    assert _junctions.read_registry(reg) == []
    assert _junctions.links_pointing_at(str(target), reg) == []


def test_an_unreadable_registry_is_an_empty_one(tmp_path):
    reg = tmp_path / "junctions.json"
    reg.write_text("{ not json")
    assert _junctions.read_registry(str(reg)) == []
    assert _junctions.read_registry(str(tmp_path / "absent.json")) == []
