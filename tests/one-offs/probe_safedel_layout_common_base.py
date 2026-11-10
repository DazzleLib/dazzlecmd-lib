"""Measure two more candidate layouts against the real trash population.

Read-only. Same population as probe_safedel_layout_pathlen.py (every manifest
in every registered store).

  base     Common-base layout (maintainer's proposal, 2026-11-09): group a
           batch's items by root (drive letter / UNC share / POSIX root); for
           each group store base = common ancestor of the items' parent dirs
           in the manifest; content path = content/<rel from base>. When a
           batch spans several roots, each group gets content/<n>/<rel>.
           A single-item batch degenerates to content/<basename> (today's
           layout).
  flat1    Claude-Code-style flattening: the whole original path squashed
           into ONE folder name (every : \\ / . -> '-'). Checked against the
           255-char per-component limit, which long-path support does NOT lift.

Predictions (written before running):
  B1  'base' stays within a few chars of 'current' for most entries; its
      over-259 count stays near 12 (current) and well below mirror's 57.
  B2  'base' gives distinct content paths for every multi-entry batch,
      including the two historical collision batches.
  F1  Some entries' flattened names exceed 255 chars (component limit), and
      the flattening maps distinct paths to one name in at least one case.
"""

import json
import ntpath
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_safedel_layout_pathlen import store_paths, deepest_inner, mirror_rel  # noqa: E402

LIMIT = 259


def root_of(p):
    drive, _ = ntpath.splitdrive(p)
    return drive.upper() if drive else "/"


def common_base(paths):
    parents = [ntpath.dirname(p) for p in paths]
    try:
        return ntpath.commonpath(parents)
    except ValueError:
        return None


def flat1(p):
    return re.sub(r"[:\\/.]", "-", p)


def main():
    counts = {"current": 0, "base": 0, "mirror": 0}
    maxes = {"current": 0, "base": 0, "mirror": 0}
    worst = {}
    deltas = []
    batches_checked = 0
    base_collisions = 0
    flat_over_255 = 0
    flat_names = {}
    flat_collisions = set()
    n = 0
    multi_root_batches = 0
    for store in store_paths():
        for name in os.listdir(store):
            folder = os.path.join(store, name)
            mpath = os.path.join(folder, "manifest.json")
            if not os.path.isfile(mpath):
                continue
            with open(mpath, encoding="utf-8") as f:
                entries = json.load(f).get("entries", [])
            if not entries:
                continue
            batches_checked += 1
            groups = {}
            for e in entries:
                groups.setdefault(root_of(e["original_path"]), []).append(e["original_path"])
            if len(groups) > 1:
                multi_root_batches += 1
            rel_of = {}
            for gi, (root, ps) in enumerate(sorted(groups.items())):
                base = common_base(ps)
                prefix = f"{gi}\\" if len(groups) > 1 else ""
                for p in ps:
                    rel = ntpath.relpath(p, base) if base else ntpath.basename(p)
                    rel_of[p] = prefix + rel
            rels = [rel_of[e["original_path"]].lower() for e in entries]
            # duplicates only matter between DIFFERENT original paths
            uniq_orig = {e["original_path"].lower() for e in entries}
            if len(set(rels)) < len(uniq_orig):
                base_collisions += 1
            cdir = len(folder) + len("\\content\\")
            for e in entries:
                n += 1
                orig = e["original_path"]
                cp = e.get("content_path")
                inner = deepest_inner(os.path.join(folder, cp)) if cp else 0
                add = (1 + inner) if inner else 0
                L = {
                    "current": cdir + len(ntpath.basename(orig)) + add,
                    "base": cdir + len(rel_of[orig]) + add,
                    "mirror": cdir + len(mirror_rel(orig)) + add,
                }
                deltas.append(L["base"] - L["current"])
                for k, v in L.items():
                    if v > LIMIT:
                        counts[k] += 1
                    if v > maxes[k]:
                        maxes[k], worst[k] = v, orig
                fl = flat1(orig)
                if len(fl) > 255:
                    flat_over_255 += 1
                prev = flat_names.get(fl.lower())
                if prev is not None and prev.lower() != orig.lower():
                    flat_collisions.add((prev, orig))
                flat_names[fl.lower()] = orig

    deltas.sort()
    print(f"entries {n}, batches {batches_checked}, multi-root batches {multi_root_batches}")
    for k in ("current", "base", "mirror"):
        print(f"  {k:8} over {LIMIT}: {counts[k]:4}   max: {maxes[k]}")
    print(f"  base - current (chars): median {deltas[len(deltas)//2]}, "
          f"p95 {deltas[int(len(deltas)*0.95)]}, max {deltas[-1]}")
    print(f"  batches where 'base' maps two distinct originals to one path: {base_collisions}")
    print(f"  flat1: names over 255 chars (component limit): {flat_over_255}")
    print(f"  flat1: distinct originals sharing one flattened name: {len(flat_collisions)}")
    for a, b in list(flat_collisions)[:3]:
        print(f"     {a}\n   = {b}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
