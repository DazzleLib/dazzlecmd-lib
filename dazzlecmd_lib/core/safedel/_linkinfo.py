"""What kind of link is this path? -- safedel's own answer, on dazzle-filekit.

Until 0.10.45 safedel imported ``dazzlecmd_lib.core.links.detect_link`` (the
engine of the ``dz links`` tool). That tied the safety code to the package
that hosts it, which blocks safedel from living in its own repository, and it
meant two implementations of "is this a junction" (the other is filekit's).

This module answers the same question with filekit's primitives:

- ``dazzle_filekit.links.detect_link_type`` -- symlink, junction or hardlink,
  decided from the link ITSELF (``lstat`` and the reparse tag), never by
  following it. A junction whose target is gone is still a junction.
- ``dazzle_filekit.links.read_link_target`` -- the stored target text, with
  the Windows ``\\\\?\\`` prefix removed.

It returns the same small record the classifier already reads (``link_type``,
``target``, ``broken``, ``link_count``, ``inode``, ``size``, ``is_dir``), so
nothing downstream changes. ``tests/test_core_safedel_linkinfo.py`` compares
it with the host's ``detect_link`` over every kind of link.

Two details are NOT filekit's and are optional here: the target written
inside a descriptor file (``.lnk``, ``.url``, ``.dazzlelink``) and the other
name of a hardlinked file. safedel only uses them for a warning line. When
the host package's link engine can be imported they are taken from it; when
it cannot (safedel on its own), the warning says less and nothing else
changes. Descriptor files are told apart by extension either way: safedel
only needs to know they are ordinary files to be moved.

Nothing here resolves a path.
"""
import os
import stat
from typing import Any, Optional

LINK_SYMLINK = "symlink"
LINK_JUNCTION = "junction"
LINK_HARDLINK = "hardlink"
LINK_SHORTCUT = "shortcut"
LINK_URLSHORTCUT = "urlshortcut"
LINK_DAZZLELINK = "dazzlelink"

_DESCRIPTOR_BY_EXTENSION = (
    (".dazzlelink", LINK_DAZZLELINK),
    (".lnk", LINK_SHORTCUT),
    (".url", LINK_URLSHORTCUT),
)


class LinkInfo:
    """One detected link. Field names match the host engine's record."""

    def __init__(self, path, name, link_type, target=None, broken=False,
                 link_count=1, inode=0, size=0, is_dir=False):
        self.path = path
        self.name = name
        self.link_type = link_type
        self.target = target
        self.broken = broken
        self.link_count = link_count
        self.inode = inode
        self.size = size
        self.is_dir = is_dir

    def __repr__(self):
        return ("LinkInfo(%r, type=%s, target=%r, broken=%s, is_dir=%s)"
                % (self.path, self.link_type, self.target, self.broken, self.is_dir))


def _host_detail(path: str) -> Optional[Any]:
    """The host package's richer record for ``path``, or None when the host
    engine is not importable or fails. Used only for warning text."""
    try:
        from dazzlecmd_lib.core.links import detect_link as host_detect
    except Exception:
        return None
    try:
        return host_detect(path)
    except Exception:
        return None


def detect_link(path: str) -> Optional[LinkInfo]:
    """A LinkInfo for ``path`` if it is a link or a descriptor file, else None."""
    from dazzle_filekit.links import detect_link_type, read_link_target

    name = os.path.basename(path)
    abs_path = os.path.abspath(path)
    try:
        lst = os.lstat(path)
    except OSError:
        return None
    inode = getattr(lst, "st_ino", 0)
    size = lst.st_size

    lowered = name.lower()
    for extension, kind in _DESCRIPTOR_BY_EXTENSION:
        if lowered.endswith(extension):
            detail = _host_detail(path)
            if detail is not None and detail.link_type == kind:
                return LinkInfo(abs_path, name, kind, target=detail.target,
                                broken=detail.broken, inode=inode, size=size,
                                is_dir=detail.is_dir)
            return LinkInfo(abs_path, name, kind, inode=inode, size=size)

    kind = detect_link_type(path)

    if kind == LINK_SYMLINK:
        target = read_link_target(path)
        return LinkInfo(
            abs_path, name, LINK_SYMLINK, target=target,
            broken=target is not None and not os.path.exists(path),
            inode=inode, size=size,
            is_dir=stat.S_ISDIR(lst.st_mode) or os.path.isdir(path))

    if kind == LINK_JUNCTION:
        target = read_link_target(path)
        return LinkInfo(
            abs_path, name, LINK_JUNCTION, target=target,
            broken=target is not None and not os.path.exists(path),
            inode=inode, size=size, is_dir=True)

    if kind == LINK_HARDLINK and stat.S_ISREG(lst.st_mode) and lst.st_nlink > 1:
        detail = _host_detail(path)
        other = detail.target if (detail is not None
                                  and detail.link_type == LINK_HARDLINK) else None
        return LinkInfo(abs_path, name, LINK_HARDLINK, target=other,
                        link_count=lst.st_nlink, inode=inode, size=size)

    return None
