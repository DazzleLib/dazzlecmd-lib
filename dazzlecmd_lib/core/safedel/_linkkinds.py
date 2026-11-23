"""What kind of link or reparse point is this, and what may a tool do with it?

One catalogue for safedel (0.10.53), vendored here first; the plan is to move
it into a DazzleLib library later (design:
2026-11-23__04-08-55__dev-workflow-process__safedel-link-kinds-catalogue-vendored-then-library.md).

Standard library only, and nothing here follows a link: ``describe`` makes one
``os.lstat`` call and opens nothing. For each kind it answers the questions a
deleting tool has to ask before it touches the item:

- is it a link / reparse point at all?
- can its target be read (and is it a directory link)?
- is it safe to READ ITS CONTENTS? (an OneDrive online-only file downloads
  when read; an app execution alias cannot be opened; a symlink's "contents"
  are somewhere else)
- is it safe to MOVE by rename? (every kind here: a rename moves the entry,
  never what it points at)

An unknown reparse tag fails safe: not readable, contents not read, moved as
is.
"""
import os
import stat
from typing import NamedTuple, Optional

FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
_NOT_LOCAL = (FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN
              | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)

TAG_SYMLINK = 0xA000000C
TAG_MOUNT_POINT = 0xA0000003        # junction
TAG_LX_SYMLINK = 0xA000001D         # WSL symlink
TAG_APPEXECLINK = 0x8000001B        # app execution alias (WindowsApps)
TAG_DEDUP = 0x80000013
TAG_CLOUD_MASK, TAG_CLOUD_BASE = 0xFFFF0FFF, 0x9000001A   # 0x9000001A, 0x9000101A, ...

# name, target readable, contents safe to read
_TAGS = {
    TAG_SYMLINK: ("symlink", True, False),
    TAG_MOUNT_POINT: ("junction", True, False),
    TAG_LX_SYMLINK: ("wsl-symlink", False, False),
    TAG_APPEXECLINK: ("app-alias", False, False),
    TAG_DEDUP: ("dedup", False, True),
}


class LinkKind(NamedTuple):
    kind: str                 # "file", "directory", "symlink", "junction", "wsl-symlink",
                              # "app-alias", "cloud-placeholder", "dedup", "unknown-reparse", "posix-symlink"
    tag: int                  # reparse tag, 0 when none
    is_link: bool             # a reparse point or POSIX symlink: moved as an entry, never followed
    node_is_dir: bool         # the entry itself is a directory (a directory symlink counts)
    target_readable: bool     # os.readlink can be expected to work
    contents_safe: bool       # reading the contents is local and harmless
    why_not_read: str         # empty when contents_safe

    def note(self) -> str:
        return "" if self.contents_safe else "not copied: %s" % self.why_not_read


def classify_stat(st) -> LinkKind:
    """The kind of an entry from its lstat result (pure; testable)."""
    attrs = getattr(st, "st_file_attributes", 0)
    tag = getattr(st, "st_reparse_tag", 0)
    node_dir = stat.S_ISDIR(st.st_mode) or bool(attrs & FILE_ATTRIBUTE_DIRECTORY)
    if stat.S_ISLNK(st.st_mode) and not attrs:
        return LinkKind("posix-symlink", 0, True, node_dir, True, False, "a symlink (its contents are elsewhere)")
    if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
        if tag in _TAGS:
            name, readable, safe = _TAGS[tag]
        elif (tag & TAG_CLOUD_MASK) == TAG_CLOUD_BASE:
            name, readable, safe = "cloud-placeholder", False, False
        else:
            name, readable, safe = "unknown-reparse", False, False
        if attrs & _NOT_LOCAL:
            safe = False
        why = "" if safe else {
            "symlink": "a symlink (its contents are elsewhere)",
            "junction": "a junction (its contents are elsewhere)",
            "wsl-symlink": "a WSL symlink",
            "app-alias": "an app execution alias (cannot be opened)",
            "cloud-placeholder": "an online-only file (reading it would download it)",
        }.get(name, "a reparse point of an unknown kind (tag 0x%08X)" % tag)
        is_link = name not in ("dedup", "cloud-placeholder")
        return LinkKind(name, tag, is_link, node_dir, readable, safe, why)
    if attrs & _NOT_LOCAL:
        return LinkKind("directory" if node_dir else "file", 0, False, node_dir, False, False,
                        "an online-only file (reading it would download it)")
    return LinkKind("directory" if node_dir else "file", 0, False, node_dir, False, True, "")


def describe(path: str) -> Optional[LinkKind]:
    """The kind of ``path`` from one lstat, or None if it does not exist."""
    try:
        return classify_stat(os.lstat(path))
    except OSError:
        return None
