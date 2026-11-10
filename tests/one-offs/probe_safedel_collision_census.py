"""Census of past safedel batches hit by the flat content/ name collision.

Read-only. Walks every trash store in the volume registry plus the central
store, loads each batch's manifest.json, and reports batches where two or
more entries share one content_path (data overwritten on Windows), or where
an entry failed to stage because its name was taken (content_path None for a
type that should have content).

Usage:
    python probe_safedel_collision_census.py            # summary
    python probe_safedel_collision_census.py --list     # every hit batch

The 2026-11-09__14-03-11 batch is the known incident; it must appear as a hit,
which is the check that the probe itself works.
"""

import argparse
import collections
import json
import os
import sys

METADATA_ONLY_TYPES = {"symlink_file", "symlink_dir", "junction", "broken_link"}


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    totals = collections.Counter()
    overwrite_hits = []   # (batch, content_path, [original paths])
    unstaged_hits = []    # (batch, original path)

    for store in store_paths():
        for name in sorted(os.listdir(store)):
            mpath = os.path.join(store, name, "manifest.json")
            if not os.path.isfile(mpath):
                totals["no_manifest"] += 1
                continue
            try:
                with open(mpath, encoding="utf-8") as f:
                    m = json.load(f)
            except (OSError, json.JSONDecodeError):
                totals["bad_manifest"] += 1
                continue
            entries = m.get("entries", [])
            totals["batches"] += 1
            totals["entries"] += len(entries)
            if len(entries) > 1:
                totals["multi_entry_batches"] += 1

            by_cp = collections.defaultdict(list)
            for e in entries:
                cp = e.get("content_path")
                if cp:
                    by_cp[cp].append(e.get("original_path"))
                elif e.get("file_type") not in METADATA_ONLY_TYPES:
                    # Expected content but none recorded: staging failed.
                    unstaged_hits.append((os.path.join(store, name), e.get("original_path")))
            for cp, origs in by_cp.items():
                if len(origs) > 1:
                    overwrite_hits.append((os.path.join(store, name), cp, origs))

    hit_batches = {b for b, _, _ in overwrite_hits}
    lost_entries = sum(len(o) - 1 for _, _, o in overwrite_hits)

    print("stores scanned:", store_paths())
    for k in ("batches", "multi_entry_batches", "entries", "no_manifest", "bad_manifest"):
        print(f"  {k:22} {totals[k]}")
    print(f"  {'overwrite batches':22} {len(hit_batches)}")
    print(f"  {'overwritten entries':22} {lost_entries}  (entries whose content was replaced by a same-named sibling)")
    print(f"  {'unstaged entries':22} {len(unstaged_hits)}  (content expected, content_path None: staging failed, original left in place)")

    known = "2026-11-09__14-03-11"
    print(f"  known incident {known} detected:", any(known in b for b in hit_batches))

    if args.list:
        print("\n-- overwrite hits --")
        for b, cp, origs in overwrite_hits:
            print(f"{b}\n   {cp}")
            for o in origs:
                print(f"     <- {o}")
        print("\n-- unstaged entries --")
        for b, o in unstaged_hits:
            print(f"{b}\n     {o}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
