"""Two measurements for the safedel content-layout decision.

Part 1 -- path shapes. Feed real-world path shapes to preservelib's existing
absolute-style mapping (dazzle_preservelib.destination.compute_destination_path,
pure string math, no I/O) and report where each one lands relative to the
destination base. Pass criterion for any mapping: the result must stay UNDER
dest_base, and two distinct sources must not map to one destination.

Part 2 -- path-length census. For every entry in every real trash manifest
(read-only), compute the longest on-disk path the entry would need under:
    current   <folder>\\content\\<basename>[\\inner...]
    index     <folder>\\content\\<NNN>\\<basename>[\\inner...]        (Option B)
    mirror    <folder>\\content\\<C>\\<full original path>[\\inner]   (Option A)
"inner" is measured by walking the staged content that exists today, so files
nested inside staged folders count. Reports how many exceed 259 chars (the
classic MAX_PATH budget) and the max for each layout.

Predictions (written before running, 2026-11-09):
  S1  UNC and \\\\?\\UNC sources escape dest_base under preservelib's mapping.
  S2  index adds <=5 chars over current; mirror adds roughly the length of
      the original parent path (40-120 chars typical here).
  S3  Some real entries exceed 259 under mirror; ~none under index.
"""

import json
import os
import sys
from pathlib import PureWindowsPath, PurePosixPath

sys.path.insert(0, r"C:\code\dazzle-preservelib")


def part1():
    from dazzle_preservelib import destination as d
    print("Part 1 -- preservelib compute_destination_path(path_style='absolute')")
    print("  module:", d.__file__)
    base = PureWindowsPath(r"D:\dst")
    shapes = [
        r"C:\Users\x\a.txt",
        r"\\server\share\dir\a.txt",
        r"//server/share/dir/a.txt",
        r"\\?\C:\Users\x\a.txt",
        r"\\?\UNC\server\share\dir\a.txt",
        r"/mnt/c/Users/x/a.txt",
        r"/home/u/a.txt",
        r"C:relative\a.txt",
        r"C:\a\..\..\..\escape.txt",
    ]
    if sys.platform != "win32":
        print("  (not on Windows; the win32 branch is what matters here)")
    for s in shapes:
        try:
            out = d.compute_destination_path(PureWindowsPath(s), base, "absolute")
            out_s = str(out)
            # lexical containment check: normalize and compare prefix
            norm = os.path.normpath(out_s)
            inside = norm.lower().startswith(str(base).lower() + "\\")
            flag = "ok " if inside else "ESCAPES dest_base"
            print(f"  {s:34} -> {out_s:44} [{flag}]")
        except Exception as e:
            print(f"  {s:34} -> EXCEPTION {type(e).__name__}: {e}")
    # collision between distinct sources
    a = d.compute_destination_path(PureWindowsPath(r"\\srvA\share\a.txt"), base, "absolute")
    b = d.compute_destination_path(PureWindowsPath(r"\\srvB\share\a.txt"), base, "absolute")
    print(f"  distinct UNC servers same tail -> same dest? {str(a) == str(b)} ({a} | {b})")


def store_paths():
    paths = []
    central = os.path.join(os.environ.get("LOCALAPPDATA", ""), "safedel", "trash")
    if os.path.isdir(central):
        paths.append(central)
    reg = os.path.join(os.path.expanduser("~"), ".safedel", "volumes.json")
    if os.path.isfile(reg):
        with open(reg, encoding="utf-8") as f:
            for info in json.load(f).values():
                p = info.get("trash_path")
                if p and os.path.isdir(p) and p not in paths:
                    paths.append(p)
    return paths


def mirror_rel(original):
    drive, rest = os.path.splitdrive(original)
    if drive.startswith("\\\\"):
        token = "UNC\\" + drive.lstrip("\\")
    else:
        token = drive.rstrip(":") or "ROOT"
    return token + "\\" + rest.lstrip("\\/")


def deepest_inner(path):
    """Longest relative path length inside a staged directory (0 for files)."""
    if not os.path.isdir(path):
        return 0
    best = 0
    plen = len(path) + 1
    for dirpath, dirnames, filenames in os.walk(path):
        for n in filenames + dirnames:
            best = max(best, len(dirpath) + 1 + len(n) - plen)
    return best


def part2():
    print("\nPart 2 -- path-length census over real trash manifests")
    LIMIT = 259
    counts = {"current": 0, "index": 0, "mirror": 0}
    maxes = {"current": 0, "index": 0, "mirror": 0}
    worst = {}
    n = 0
    for store in store_paths():
        for name in os.listdir(store):
            folder = os.path.join(store, name)
            mpath = os.path.join(folder, "manifest.json")
            if not os.path.isfile(mpath):
                continue
            with open(mpath, encoding="utf-8") as f:
                m = json.load(f)
            entries = m.get("entries", [])
            width = max(3, len(str(max(len(entries) - 1, 0))))
            for e in entries:
                n += 1
                orig = e.get("original_path") or ""
                cp = e.get("content_path")
                inner = deepest_inner(os.path.join(folder, cp)) if cp else 0
                inner_add = (1 + inner) if inner else 0
                cdir = len(folder) + len("\\content\\")
                L = {
                    "current": cdir + len(os.path.basename(orig)) + inner_add,
                    "index": cdir + width + 1 + len(os.path.basename(orig)) + inner_add,
                    "mirror": cdir + len(mirror_rel(orig)) + inner_add,
                }
                for k, v in L.items():
                    if v > LIMIT:
                        counts[k] += 1
                    if v > maxes[k]:
                        maxes[k] = v
                        worst[k] = orig
    print(f"  entries measured: {n}")
    for k in ("current", "index", "mirror"):
        print(f"  {k:8} entries over {LIMIT}: {counts[k]:5}   max length: {maxes[k]}   worst: {worst.get(k, '')[:90]}")


if __name__ == "__main__":
    part1()
    part2()
