r"""Does safedel delete and recover items whose paths are longer than 260 characters?

Builds a folder chain deeper than MAX_PATH under argv[1] (which must not
exist), with its own trash store inside it, so the real trash is never used.
Tries: the file at the bottom, and the top folder holding the chain. Prints
what happens and where it fails. On Windows without LongPathsEnabled, only
paths with the \\?\ prefix can reach that deep.
"""
import os
import sys
import traceback

PREFIX = "\\\\?\\"


def X(p):
    return p if p.startswith(PREFIX) else PREFIX + p


def main(root):
    root = os.path.abspath(root)
    if os.path.exists(root):
        raise SystemExit("refusing: %s exists" % root)
    os.makedirs(root)
    import dazzlecmd_lib
    import dazzlecmd_lib.core.safedel as sd
    print("dazzlecmd-lib", dazzlecmd_lib.__version__)

    deep = root
    while len(deep) < 300:
        deep = os.path.join(deep, "a_fairly_long_folder_name_segment")
        os.mkdir(X(deep))
    f = os.path.join(deep, "file.txt")
    with open(X(f), "w") as fh:
        fh.write("long")
    print("deepest path length:", len(f), "| plain exists():", os.path.exists(f),
          "| prefixed exists():", os.path.exists(X(f)))

    store = sd.TrashStore(store_path=os.path.join(root, "trash"),
                          registry_path=os.path.join(root, "reg.json"))
    top = os.path.join(root, "a_fairly_long_folder_name_segment")
    for label, target, cf in (("file at the bottom (plain path)", f, False),
                              ("file at the bottom (\\\\?\\ path)", X(f), False),
                              ("top folder holding the chain", top, False),
                              ("top folder, with copy-first", top, True)):
        try:
            r = store.trash([target], copy_first=cf, copy_first_max=10 ** 9)
            if cf and r.success:
                cfdir = os.path.join(root, "trash", r.folder_name, "copy-first")
                n = sum(len(fs) for _d, _s, fs in os.walk(X(cfdir)))
                print(label, "-> copy-first files:", n, "| record:",
                      [e.copy_first for e in r.entries][:1])
            print(label, "-> staged:", r.success, [str(e)[:140] for e in r.errors[:1]])
            if r.success:
                rc = sd.recover_folder(store, r.folder_name)
                print(label, "-> recover rc:", rc, "| file back:", os.path.exists(X(f)))
        except Exception as exc:  # noqa: BLE001 -- a probe reports everything
            tb = traceback.extract_tb(exc.__traceback__)[-1]
            print(label, "-> EXCEPTION", type(exc).__name__, str(exc)[:150])
            print("    at", tb.filename, tb.lineno)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
