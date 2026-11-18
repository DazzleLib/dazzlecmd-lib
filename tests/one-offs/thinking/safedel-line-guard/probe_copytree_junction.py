"""Probe: what does a tree copy do with a junction inside the tree?

Question: `dazzle_filekit.operations.copy_tree_preserving_links` wraps
`shutil.copytree(symlinks=True)` and its docstring says a junction is copied
as a LINK. Does it, on this Python on Windows -- or does it enter the
junction and copy what it points at?

Prediction (written before the first run, 2026-11-18): it ENTERS the junction
and copies the target's files as ordinary files (shutil treats a junction as
a directory, not a symlink).

Everything happens under a fresh temp folder; nothing is deleted (the folder
is left for inspection and printed). The junction is made with the `_winapi`
call Python itself uses in its tests -- no shell, no resolving.

Usage:  python -I probe_copytree_junction.py     (use -I off if filekit is an
editable install that -I hides; the script prints which filekit it imported)
"""
import os
import shutil
import stat
import sys
import tempfile


def is_reparse(path):
    st = os.lstat(path)
    return bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def main():
    if sys.platform != "win32":
        print("Windows only (junctions).")
        return 0
    import _winapi
    root = tempfile.mkdtemp(prefix="dz-probe-copytree-")
    target = os.path.join(root, "TARGET-outside-the-tree")
    src = os.path.join(root, "src")
    os.makedirs(target)
    os.makedirs(src)
    with open(os.path.join(target, "precious.txt"), "w") as fh:
        fh.write("this file lives OUTSIDE src; a link-preserving copy must not duplicate it\n")
    with open(os.path.join(src, "ordinary.txt"), "w") as fh:
        fh.write("ordinary\n")
    link = os.path.join(src, "private")
    _winapi.CreateJunction(target, link)
    print("python", sys.version.split()[0])
    print("root:", root)
    print("src/private is a reparse point:", is_reparse(link), "-> ", os.readlink(link))

    results = {}

    # Arm 1: plain shutil.copytree(symlinks=True)
    dst1 = os.path.join(root, "dst-shutil-symlinks-true")
    shutil.copytree(src, dst1, symlinks=True)
    p1 = os.path.join(dst1, "private")
    results["shutil.copytree(symlinks=True)"] = (
        os.path.lexists(p1), os.path.lexists(p1) and is_reparse(p1),
        os.path.exists(os.path.join(p1, "precious.txt")) and not (os.path.lexists(p1) and is_reparse(p1)))

    # Arm 2: filekit's wrapper
    try:
        import dazzle_filekit
        from dazzle_filekit.operations import copy_tree_preserving_links
        print("filekit", dazzle_filekit.__version__, "from", os.path.dirname(dazzle_filekit.__file__))
        dst2 = os.path.join(root, "dst-filekit")
        copy_tree_preserving_links(src, dst2)
        p2 = os.path.join(dst2, "private")
        results["filekit.copy_tree_preserving_links"] = (
            os.path.lexists(p2), os.path.lexists(p2) and is_reparse(p2),
            os.path.exists(os.path.join(p2, "precious.txt")) and not (os.path.lexists(p2) and is_reparse(p2)))
    except Exception as exc:
        print("filekit arm failed:", repr(exc))

    # Arm 3 (control): safedel's own copier, which must record the link and not enter it
    try:
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))
        from dazzlecmd_lib.core.safedel._copyfirst import copy_first
        dst3 = os.path.join(root, "dst-safedel-copyfirst")
        rec = copy_first(src, dst3, True)
        p3 = os.path.join(dst3, "private")
        results["safedel._copyfirst.copy_first"] = (os.path.lexists(p3), False, False)
        print("copy_first record:", rec)
    except Exception as exc:
        print("copy_first arm failed:", repr(exc))

    print()
    print("%-40s %-14s %-16s %s" % ("copier", "private exists", "is a junction", "TARGET FILE DUPLICATED INTO COPY"))
    for name, (exists, reparse, duplicated) in results.items():
        print("%-40s %-14s %-16s %s" % (name, exists, reparse, duplicated))
    print("\ntarget still intact:", os.path.exists(os.path.join(target, "precious.txt")))
    print("(folder left in place for inspection)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
