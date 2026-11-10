"""Measure the safedel name-collision mechanism, end to end, in a temp sandbox.

Read-only toward the real system: every path lives under a fresh temp dir,
TrashStore gets an explicit store_path AND registry_path (registry_path set
skips per-volume routing, so nothing touches ~/.safedel or a .safedel-trash).

Predictions written before running (2026-11-09):
  P1  Windows: os.rename(file, existing_file) raises FileExistsError
      (errno 17 EEXIST, winerror 183), not EXDEV.
  P2  shutil.copy2 onto an existing file overwrites it silently.
  P3  TrashStore.trash([a/x.json, b/x.json]) -> one batch, both entries share
      content_path 'content/x.json'; only b's bytes survive in the trash;
      both originals are gone. result.success is True (no error reported).
  P4  Folders: trash([a/d, b/d]) -> second folder fails to stage and is left
      in place; result.success is False.
  P5  Control arm (distinct names) -> both survive, success True.

Run against the SOURCE tree: python probe_safedel_collision_mechanism.py
"""

import errno
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from dazzlecmd_lib.core.safedel import TrashStore  # noqa: E402
import dazzlecmd_lib.core.safedel as sd  # noqa: E402


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def read(path):
    with open(path, "rb") as f:
        return f.read()


def p1_p2(root):
    a = os.path.join(root, "p1", "a.txt")
    b = os.path.join(root, "p1", "b.txt")
    write(a, b"AAAA")
    write(b, b"BB")
    try:
        os.rename(a, b)
        p1 = f"rename SUCCEEDED (silent replace); b now={read(b)!r}"
    except OSError as e:
        p1 = (f"{type(e).__name__} errno={e.errno} ({errno.errorcode.get(e.errno)}) "
              f"winerror={getattr(e, 'winerror', None)}")
    print("P1 rename onto existing file:", p1)
    write(a, b"AAAA")
    shutil.copy2(a, b)
    print("P2 copy2 onto existing file: b now =", read(b), "(overwritten)" if read(b) == b"AAAA" else "")


def make_store(root, tag):
    store_dir = os.path.join(root, f"store_{tag}")
    reg = os.path.join(root, f"registry_{tag}.json")
    return TrashStore(store_path=store_dir, registry_path=reg)


def p3_files(root, names, tag):
    src = []
    for i, (sub, name) in enumerate(names):
        p = os.path.join(root, tag, sub, name)
        write(p, f"content-{sub}-{i}".encode() * (i + 1))
        src.append(p)
    expected = {p: read(p) for p in src}
    res = make_store(root, tag).trash(src)
    print(f"\n[{tag}] success={res.success} errors={res.errors}")
    survivors = 0
    for e in res.entries:
        cp = os.path.join(res.folder_path, e.content_path) if e.content_path else None
        got = read(cp) if cp and os.path.isfile(cp) else None
        ok = got == expected[e.original_path]
        survivors += ok
        print(f"   {e.content_path!s:28} orig_exists={os.path.exists(e.original_path)!s:5} "
              f"content_matches_original={ok}")
    print(f"   -> {survivors}/{len(src)} entries recoverable with their own bytes")


def p4_dirs(root):
    tag = "dirs"
    src = []
    for sub in ("a", "b"):
        d = os.path.join(root, tag, sub, "d")
        write(os.path.join(d, f"inner_{sub}.txt"), sub.encode())
        src.append(d)
    res = make_store(root, tag).trash(src)
    print(f"\n[{tag}] success={res.success}")
    for e in res.entries:
        print(f"   {e.original_path[-12:]:14} content_path={e.content_path!s:12} "
              f"left_in_place={os.path.exists(e.original_path)}")
    for err in res.errors:
        print("   error:", err[:140])


def main():
    print("safedel loaded from:", sd.__file__)
    root = tempfile.mkdtemp(prefix="sd_collision_probe_")
    try:
        p1_p2(root)
        p3_files(root, [("a", "x.json"), ("b", "x.json"), ("c", "x.json")], "same_name")
        p3_files(root, [("a", "x.json"), ("b", "y.json")], "control_distinct")
        p4_dirs(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
