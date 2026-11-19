"""Show what `recover` does with a batch from a run that did not finish.

Builds a throwaway trash store in a NEW folder, deletes two scratch files
into it, and makes the batch look as an interrupted run leaves it (the
manifest still says "staging" and lists only the item that had moved). Then
prints exactly what `recover last` and `recover <batch name>` print -- the
same functions the `dz safedel recover` command calls.

Everything is inside the folder you name; it must not exist. Nothing else on
the machine is read or written.

Usage: python tests/one-offs/probe_recover_unfinished_batch.py <new scratch folder>
"""
import json
import os
import sys

import dazzlecmd_lib
import dazzlecmd_lib.core.safedel as sd


def main(argv):
    if len(argv) != 2 or os.path.lexists(argv[1]):
        print(__doc__)
        return 2
    root = os.path.abspath(argv[1])
    src = os.path.join(root, "work")
    os.makedirs(src)
    for name, data in (("a.txt", b"A"), ("b.txt", b"B")):
        with open(os.path.join(src, name), "wb") as fh:
            fh.write(data)
    store = sd.TrashStore(store_path=os.path.join(root, "trash"),
                          registry_path=os.path.join(root, "reg.json"))
    result = store.trash([os.path.join(src, "a.txt")])
    # as an interrupted run leaves it: a.txt moved, b.txt never reached,
    # the manifest never marked complete
    manifest = os.path.join(result.folder_path, "manifest.json")
    with open(manifest, encoding="utf-8") as fh:
        m = json.load(fh)
    m["state"] = "staging"
    with open(manifest, "w", encoding="utf-8") as fh:
        json.dump(m, fh, indent=2)

    print("library:", dazzlecmd_lib.__version__)
    print("batch:", result.folder_name, "(manifest state: staging)")
    print("on disk: a.txt in work? %s   b.txt in work? %s" % (
        os.path.exists(os.path.join(src, "a.txt")), os.path.exists(os.path.join(src, "b.txt"))))
    for args in (["last"], [result.folder_name]):
        print("\n$ dz safedel recover " + " ".join(args))
        sys.stdout.flush()
        rc = sd.cmd_recover(store, args)
        sys.stdout.flush()
        sys.stderr.flush()
        print("[exit code %d]" % rc)
    print("\non disk afterwards: a.txt in work? %s" % os.path.exists(os.path.join(src, "a.txt")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
