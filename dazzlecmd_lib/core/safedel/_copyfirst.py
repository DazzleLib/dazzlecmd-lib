"""Survey an item, and make a hash-verified copy of it before it is moved.

Two jobs that walk the same tree the same careful way -- never following a
link:

``survey(path)`` counts what an item holds (files, folders, links, hidden and
system entries, hard-linked files, bytes). The preview prints it so a person
sees what kind of thing they are about to move, and the copy-first size limit
is checked against its byte count.

``copy_first(path, dest)`` is safedel's "training wheels": while the tool's
own correctness is still being proven, an item is COPIED into the batch
(``<batch>/copy-first/<name>``) and the copy's content verified against the
source by SHA-256 before the original is renamed into ``content/``. The
renamed original remains the faithful item -- a rename keeps every
timestamp, ACL and stream -- and the copy is a second net if something
downstream goes wrong. A hash proves CONTENT is identical and nothing more:
the copy's creation time is new, and on a cross-device copy some metadata is
not carried.

Links are never copied through: a link inside a directory is copied as a
link and compared by its recorded target. A link given as the item itself
gets no copy at all (the caller does not ask for one).
"""

import hashlib
import os
import shutil
import stat
import sys
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_MAX_BYTES = 10 * 1024 * 1024   # the maintainer's first figure: 10 MB

_HIDDEN = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)
_SYSTEM = getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0x4)
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _attrs(st) -> int:
    return getattr(st, "st_file_attributes", 0) or 0


def _is_link(st) -> bool:
    """A symlink, or (Windows) any reparse point such as a junction."""
    return stat.S_ISLNK(st.st_mode) or bool(_attrs(st) & _REPARSE)


def _is_hidden(name: str, st) -> bool:
    if sys.platform == "win32":
        return bool(_attrs(st) & _HIDDEN)
    return name.startswith(".")


def survey(path: str) -> Dict[str, Any]:
    """Count what ``path`` holds, without following any link.

    Returns ``files``, ``dirs``, ``links``, ``hidden``, ``system``,
    ``hardlinked`` (files with more than one name), ``bytes`` (regular files
    only) and ``unreadable`` (folders that could not be listed). For a single
    file the counts describe that file. The item itself is included in the
    hidden/system counts and, when it is a link, counted as one link and not
    entered.
    """
    out = {"files": 0, "dirs": 0, "links": 0, "hidden": 0, "system": 0,
           "hardlinked": 0, "bytes": 0, "unreadable": 0}

    def note(name, st):
        if _is_hidden(name, st):
            out["hidden"] += 1
        if _attrs(st) & _SYSTEM:
            out["system"] += 1

    try:
        top = os.lstat(path)
    except OSError:
        return out
    note(os.path.basename(path.rstrip("\\/")), top)

    if _is_link(top):
        out["links"] += 1
        return out
    if not stat.S_ISDIR(top.st_mode):
        out["files"] += 1
        out["bytes"] += top.st_size
        if top.st_nlink > 1:
            out["hardlinked"] += 1
        return out

    stack = [path]
    while stack:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            out["unreadable"] += 1
            continue
        for entry in entries:
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                out["unreadable"] += 1
                continue
            note(entry.name, st)
            if _is_link(st):
                out["links"] += 1            # counted, never entered
            elif stat.S_ISDIR(st.st_mode):
                out["dirs"] += 1
                stack.append(entry.path)
            else:
                out["files"] += 1
                out["bytes"] += st.st_size
                if st.st_nlink > 1:
                    out["hardlinked"] += 1
    return out


def format_survey(s: Dict[str, Any], is_dir: bool) -> str:
    """One line for the preview: what kind of content an item holds."""
    parts: List[str] = []
    if is_dir:
        parts.append("%d file(s), %d folder(s)" % (s["files"], s["dirs"]))
    if s["links"]:
        parts.append("%d link(s) (moved as links, never followed)" % s["links"])
    if s["hidden"]:
        parts.append("%d hidden" % s["hidden"])
    if s["system"]:
        parts.append("%d system" % s["system"])
    if s["hardlinked"]:
        parts.append("%d hard-linked" % s["hardlinked"])
    if s["unreadable"]:
        parts.append("%d unreadable" % s["unreadable"])
    parts.append(format_bytes(s["bytes"]))
    return ", ".join(parts)


def format_bytes(n: int) -> str:
    if n < 1024:
        return "%d bytes" % n
    if n < 1024 * 1024:
        return "%.1f KB" % (n / 1024.0)
    if n < 1024 * 1024 * 1024:
        return "%.1f MB" % (n / (1024.0 * 1024))
    return "%.1f GB" % (n / (1024.0 * 1024 * 1024))


def parse_size(text: str) -> int:
    """'10MB', '512k', '1g', '4096' -> bytes. Raises ValueError otherwise."""
    t = str(text).strip().lower().replace(" ", "")
    units = (("gb", 1024 ** 3), ("mb", 1024 ** 2), ("kb", 1024),
             ("g", 1024 ** 3), ("m", 1024 ** 2), ("k", 1024), ("b", 1))
    for suffix, factor in units:
        if t.endswith(suffix):
            number = t[: -len(suffix)]
            return int(float(number) * factor)
    return int(t)


def _sha256(path: str) -> str:
    # Deliberately not dazzle_filekit.verification.calculate_file_hash
    # (considered 2026-11-19): that returns {} for a missing file and logs
    # and swallows a read error, so a caller comparing two results would see
    # two unreadable files as "equal". Verification here must FAIL when a file
    # cannot be read, so this raises. Adopt filekit's only if it gains a
    # raising mode.
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _copy_tree(src: str, dst: str) -> Tuple[int, int, List[Dict[str, str]]]:
    """Copy the tree at ``src`` to ``dst`` and verify each file as it lands.

    Its own walk, on purpose: it never enters a link. (``shutil.copytree``
    treats a Windows junction as a folder and copies what it points at, so a
    folder holding a junction would pull the junction's whole target into
    the copy.) A link inside the tree is not copied; it is recorded -- the
    renamed original still carries the link itself.

    Returns (files verified, bytes verified, links recorded). Raises OSError
    naming the first failure.
    """
    files = 0
    total = 0
    links: List[Dict[str, str]] = []
    os.mkdir(dst)
    stack = [(src, dst, "")]
    while stack:
        s_dir, d_dir, rel = stack.pop()
        for entry in os.scandir(s_dir):
            s_path = entry.path
            d_path = os.path.join(d_dir, entry.name)
            r_path = (rel + "/" + entry.name) if rel else entry.name
            st = entry.stat(follow_symlinks=False)
            if _is_link(st):
                try:
                    target = os.readlink(s_path)
                except (OSError, NotImplementedError):
                    target = ""
                links.append({"path": r_path, "target": target})
            elif stat.S_ISDIR(st.st_mode):
                os.mkdir(d_path)
                stack.append((s_path, d_path, r_path))
            else:
                shutil.copy2(s_path, d_path)
                if _sha256(s_path) != _sha256(d_path):
                    raise OSError("copy differs from the original: %s" % s_path)
                files += 1
                total += st.st_size
        try:
            shutil.copystat(s_dir, d_dir)
        except OSError:
            pass   # folder times on the copy are best effort
    return files, total, links


def copy_first(path: str, dest: str, is_dir: bool) -> Dict[str, Any]:
    """Copy ``path`` to ``dest`` and verify the copy's content.

    ``dest`` must not exist. On any failure the partial copy is removed and
    OSError is raised: the caller then leaves the original where it is.

    Returns the record stored in the manifest entry: the algorithm, how many
    files and bytes were verified, and any links inside the item that were
    recorded and not copied.
    """
    if os.path.lexists(dest):
        raise OSError("copy-first destination already exists: %s" % dest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    links: List[Dict[str, str]] = []
    try:
        if is_dir:
            files, total, links = _copy_tree(path, dest)
        else:
            shutil.copy2(path, dest)
            if _sha256(path) != _sha256(dest):
                raise OSError("copy differs from the original: %s" % path)
            files, total = 1, os.lstat(path).st_size
    except Exception as exc:
        _discard(dest)
        if isinstance(exc, OSError):
            raise
        raise OSError("copy-first failed for %s: %s" % (path, exc))
    record = {"algorithm": "SHA256", "files": files, "bytes": total,
              "verified": True}
    if links:
        record["links_not_copied"] = links
    return record


def _discard(dest: str) -> None:
    """Remove a partial copy. ``dest`` is always inside the batch folder this
    run created, under ``copy-first/``."""
    try:
        st = os.lstat(dest)
    except OSError:
        return
    try:
        if stat.S_ISDIR(st.st_mode) and not _is_link(st):
            shutil.rmtree(dest)
        else:
            os.unlink(dest)
    except OSError:
        pass
