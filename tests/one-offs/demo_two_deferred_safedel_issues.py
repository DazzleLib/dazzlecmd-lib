"""Show the two safedel issues deferred from 0.10.50, on throwaway data.

Everything is created under the folder given as argv[1], which must not
exist; the trash store is inside it too (TrashStore(store_path=...)), so the
real trash is never touched. Prints what a person would see.

  1. A broken DIRECTORY symlink is deleted and recovered: does it come back
     as a directory symlink?
  2. Copy-first fails partway through copying a folder that holds a
     read-only file: is the partial copy cleaned up?
"""
import os
import stat
import sys


def main(root):
    if os.path.exists(root):
        raise SystemExit("refusing: %s already exists" % root)
    os.makedirs(root)
    import dazzlecmd_lib
    import dazzlecmd_lib.core.safedel as sd
    from dazzlecmd_lib.core.safedel import _copyfirst
    print("dazzlecmd-lib", dazzlecmd_lib.__version__)

    store = sd.TrashStore(store_path=os.path.join(root, "trash"),
                          registry_path=os.path.join(root, "reg.json"))

    # ---- 1. broken directory symlink
    print("\n=== 1. a broken directory symlink")
    work = os.path.join(root, "work1")
    os.makedirs(work)
    link = os.path.join(work, "old-build")
    os.symlink(os.path.join(work, "build-that-was-deleted"), link, target_is_directory=True)
    attrs = os.lstat(link).st_file_attributes
    print("before: is a directory link:", bool(attrs & 0x10), "| target exists:", os.path.exists(link))
    r = store.trash([link])
    print("deleted:", r.success, r.folder_name)
    sd.recover_folder(store, r.folder_name)
    attrs = os.lstat(link).st_file_attributes
    print("after recovery: is a directory link:", bool(attrs & 0x10),
          "| link target:", os.readlink(link))
    print("cmd's dir would show it as:", "<SYMLINKD>" if attrs & 0x10 else "<SYMLINK>")

    # ---- 2. copy-first fails partway; a read-only file is in the partial copy
    print("\n=== 2. copy-first fails partway through a folder with a read-only file")
    src = os.path.join(root, "work2", "repo")
    os.makedirs(os.path.join(src, ".git", "objects"))
    ro = os.path.join(src, ".git", "objects", "aa01")
    with open(ro, "w") as fh:
        fh.write("object")
    os.chmod(ro, stat.S_IREAD)                                  # like a git object
    for i in range(3):
        with open(os.path.join(src, "z%02d.txt" % i), "w") as fh:
            fh.write("x" * i)

    real_sha = _copyfirst._sha256
    seen = {"n": 0}

    def flaky_sha(path):                                        # the copy goes bad on the 3rd file
        seen["n"] += 1
        if seen["n"] == 5:
            raise OSError("simulated: read error while verifying")
        return real_sha(path)
    _copyfirst._sha256 = flaky_sha
    try:
        r = store.trash([src], copy_first=True, copy_first_max=10 ** 9)
    finally:
        _copyfirst._sha256 = real_sha
    print("delete result: success =", r.success, "| errors:", [str(e)[:90] for e in r.errors])
    print("original still in place:", os.path.isdir(src),
          "with", sum(len(f) for _d, _s, f in os.walk(src)), "files")
    left = []
    for d, _s, fs in os.walk(os.path.join(root, "trash")):
        for f in fs:
            if f != "manifest.json":
                left.append(os.path.relpath(os.path.join(d, f), os.path.join(root, "trash")))
    print("left in the trash after the failed copy:", left or "nothing")

    # make the scratch removable later
    for d, _s, fs in os.walk(root):
        for f in fs:
            try:
                os.chmod(os.path.join(d, f), stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
