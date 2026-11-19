"""Recover a COPY of an old shared-name batch, into a scratch folder.

Issue #7 asks that the old-batch rule be verified on a copy of the
2026-11-09 incident batch: several manifest entries pointing at one stored
item. This copies that batch folder into a NEW scratch folder, opens a store
on the copy, and recovers with --to another scratch folder. The original
batch is only read (one directory copy); nothing in any real trash store is
written, moved or removed, and nothing is recovered to an original path.

Usage:
  python probe_recover_incident_batch_copy.py <batch folder> <new scratch folder> [--dry-run]

The scratch folder must not exist. The lib under test is whatever is first
on PYTHONPATH (run with PYTHONPATH=<checkout> to test uncommitted source).
"""
import json
import os
import shutil
import sys


def main(argv):
    args = [a for a in argv[1:] if a != "--dry-run"]
    dry_run = "--dry-run" in argv
    if len(args) != 2:
        print(__doc__)
        return 2
    batch, scratch = os.path.abspath(args[0]), os.path.abspath(args[1])
    if os.path.lexists(scratch):
        print("refusing:", scratch, "already exists")
        return 2
    if not os.path.isfile(os.path.join(batch, "manifest.json")):
        print("refusing:", batch, "has no manifest.json")
        return 2

    store_dir = os.path.join(scratch, "store")
    name = os.path.basename(batch)
    # symlinks=True: a link in the batch is copied as a link, never entered
    shutil.copytree(batch, os.path.join(store_dir, name), symlinks=True)

    import dazzlecmd_lib
    import dazzlecmd_lib.core.safedel as sd
    print("library:", os.path.dirname(dazzlecmd_lib.__file__))
    with open(os.path.join(store_dir, name, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    print("entries in the manifest: %d" % len(manifest.get("entries", [])))
    for e in manifest.get("entries", []):
        print("   %-12s %s  -> %s" % (e.get("file_type"), e.get("original_name"),
                                      e.get("content_path")))
    print("stored under content/:", sorted(os.listdir(os.path.join(store_dir, name, "content"))))

    store = sd.TrashStore(store_path=store_dir,
                          registry_path=os.path.join(scratch, "reg.json"))
    out = os.path.join(scratch, "recovered")
    print("--- recover (dry_run=%s) to %s ---" % (dry_run, out))
    sys.stdout.flush()
    rc = sd.recover_folder(store, name, to_path=out, dry_run=dry_run)
    sys.stderr.flush()
    print("--- exit code:", rc)
    if os.path.isdir(out):
        # top level only: a recovered folder can hold hundreds of files and
        # paths longer than Windows' plain-path limit
        for name_ in sorted(os.listdir(out)):
            p = os.path.join(out, name_)
            if os.path.isdir(p):
                print("   recovered folder: %s (%d entries)" % (name_, len(os.listdir(p))))
            else:
                print("   recovered file:   %s (%d bytes)" % (name_, os.path.getsize(p)))
    print("copy of the batch still exists:", os.path.isdir(os.path.join(store_dir, name)))
    print("left in place:", scratch)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
