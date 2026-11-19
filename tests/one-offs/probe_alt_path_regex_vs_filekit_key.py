"""Does filekit's path key give the same "other runtime" path as safedel's regex?

READ-ONLY. Opens manifest.json files in the trash stores for reading and
compares, for every entry, three values:

  stored   -- the manifest's own ``original_path_alt``
  old      -- the regex safedel used through 0.10.42 (copied below)
  new      -- encode_portable_path + decode_portable_path (dazzle-filekit)

Prints the counts and every difference. Writes nothing, moves nothing.

Usage:  python probe_alt_path_regex_vs_filekit_key.py [store ...]
"""
import json
import os
import re
import sys

from dazzle_filekit.portable import (
    PortablePathError, decode_portable_path, encode_portable_path,
)


def old_windows(path):
    m = re.match(r"^([a-zA-Z]):[\\/](.*)$", path)
    if m:
        return "/mnt/%s/%s" % (m.group(1).lower(), m.group(2).replace("\\", "/"))
    return None


def old_posix(path):
    m = re.match(r"^/mnt/([a-zA-Z])(/.*)?$", path)
    if m:
        return "%s:%s" % (m.group(1).upper(), (m.group(2) or "").replace("/", "\\"))
    return None


def new_windows(path):
    if not re.match(r"^[a-zA-Z]:[\\/]", path):
        return None
    try:
        return decode_portable_path(encode_portable_path(path, flavor="windows"), runtime="wsl")
    except PortablePathError as e:
        return "ERROR %s" % e


def new_posix(path):
    if not re.match(r"^/mnt/[a-zA-Z](/.*)?$", path):
        return None
    try:
        return decode_portable_path(encode_portable_path(path, flavor="wsl"), runtime="windows")
    except PortablePathError as e:
        return "ERROR %s" % e


EDGE = [
    "C:\\", "C:/", "c:\\Users\\x", "C:\\a\\..\\b", "C:\\a\\.\\b", "C:\\a\\\\b",
    "C:\\a\\b\\", "C:\\a b\\c.txt", "C:\\a\\b.", "C:\\a\\b ", "\\\\?\\C:\\a\\b",
    "\\\\server\\share\\a", "C:rel", "C:\\CON", "C:\\a\\nul.txt", "C:\\\u00e9\\x",
    "/mnt/c", "/mnt/c/", "/mnt/c/Users/x", "/mnt/C/Users/x", "/mnt/c/a/../b",
    "/mnt/c/a//b", "/mnt/cc/a", "/home/x", "/c/Users/x",
]


def main(argv):
    stores = argv[1:] or [
        os.path.expanduser("~/.safedel-trash"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "safedel", "trash"),
        "E:\\.safedel-trash",
    ]
    entries = same_new = same_old = 0
    diffs = []
    for store in stores:
        if not os.path.isdir(store):
            continue
        for name in sorted(os.listdir(store)):
            mf = os.path.join(store, name, "manifest.json")
            try:
                with open(mf, "r", encoding="utf-8") as fh:
                    manifest = json.load(fh)
            except (OSError, ValueError):
                continue
            for e in manifest.get("entries", []):
                orig = e.get("original_path")
                if not isinstance(orig, str):
                    continue
                entries += 1
                stored = e.get("original_path_alt")
                if orig.startswith("/"):
                    old, new = old_posix(orig), new_posix(orig)
                else:
                    old, new = old_windows(orig), new_windows(orig)
                same_old += (old == stored)
                same_new += (new == stored)
                if new != old or new != stored:
                    diffs.append((name, orig, stored, old, new))
    print("entries: %d   old == stored: %d   new == stored: %d   differing: %d"
          % (entries, same_old, same_new, len(diffs)))
    for d in diffs[:40]:
        print("  DIFF batch=%s\n    orig  =%r\n    stored=%r\n    old   =%r\n    new   =%r" % d)
    print("\nedge cases (old -> new):")
    for p in EDGE:
        if p.startswith("/"):
            old, new = old_posix(p), new_posix(p)
        else:
            old, new = old_windows(p), new_windows(p)
        print("  %-22r %-28r %-28r %s" % (p, old, new, "" if old == new else "<-- differs"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
