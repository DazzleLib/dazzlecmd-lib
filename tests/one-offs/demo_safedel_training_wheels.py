"""Show, with real output, how dz safedel's newer parts behave (2026-11-23).

Everything runs in a fresh temporary folder with its own trash store; the real
store and the real disk are never touched. The guard part only calls the pure
judge() function, which executes nothing.

    python tests/one-offs/demo_safedel_training_wheels.py
"""
import os
import subprocess
import sys
import tempfile

from dazzlecmd_lib.core.safedel import TrashStore
from dazzlecmd_lib.core.safedel._guard import judge


def show(title):
    print("\n" + "=" * 70 + "\n" + title + "\n" + "=" * 70)


def report(result):
    print("  success:", result.success)
    for e in result.entries:
        print("  staged:", os.path.basename(e.original_path), "| copy-first:",
              "verified copy" if e.copy_first else "none")
    for x in result.errors:
        print("  ERROR:", x)
    for w in result.warnings:
        print("  WARNING:", w)


root = tempfile.mkdtemp(prefix="safedel-demo-")
store = TrashStore(store_path=os.path.join(root, "trash"),
                   registry_path=os.path.join(root, "registry.json"))
work = os.path.join(root, "work")
os.makedirs(work)

# 1. The original design: move it away, bring it back.
show("1. Plain move (the original design), copy-first off")
f = os.path.join(work, "notes.txt")
open(f, "w").write("hello\n")
r = store.trash([f], copy_first=False)
report(r)
print("  original still on disk:", os.path.exists(f))
print("  stored in the batch as:", r.entries[0].content_path)

# 2. Copy-first on a small folder.
show("2. Copy-first on a small folder (what an agent gets by default)")
d = os.path.join(work, "project")
os.makedirs(os.path.join(d, "src"))
open(os.path.join(d, "src", "a.py"), "w").write("print(1)\n")
r = store.trash([d], copy_first=True)
report(r)
cf = os.path.join(r.folder_path, "copy-first")
print("  copy-first folder holds:", sorted(os.listdir(cf)) if os.path.isdir(cf) else "nothing")

# 3. Copy-first over its 10 MB limit.
show("3. Copy-first over its 10 MB limit")
big = os.path.join(work, "big.bin")
with open(big, "wb") as fh:
    fh.write(b"\0" * (11 * 1024 * 1024))
r = store.trash([big], copy_first=True)
report(r)
print("  big.bin still in place:", os.path.exists(big))
r = store.trash([big], copy_first=True, copy_first_max=20 * 1024 * 1024)
print("  -- again with the limit raised to 20 MB:")
report(r)

# 4. A junction inside a folder: moved as a link, never followed.
show("4. A folder holding a junction to a real folder elsewhere")
real = os.path.join(root, "elsewhere", "real-data")
os.makedirs(real)
open(os.path.join(real, "precious.txt"), "w").write("keep me\n")
holder = os.path.join(work, "holder")
os.makedirs(holder)
link = os.path.join(holder, "link-to-real")
if os.name == "nt":
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "New-Item -ItemType Junction -Path '%s' -Target '%s' | Out-Null" % (link, real)],
                   check=True)
else:
    os.symlink(real, link)
r = store.trash([holder], copy_first=True)
report(r)
print("  real folder's file still there:", os.path.exists(os.path.join(real, "precious.txt")))

# 5. Same names side by side.
show("5. Two files with the same name in one delete")
for sub in ("a", "b"):
    os.makedirs(os.path.join(work, "pair", sub))
    open(os.path.join(work, "pair", sub, "x.json"), "w").write(sub)
r = store.trash([os.path.join(work, "pair", "a", "x.json"),
                 os.path.join(work, "pair", "b", "x.json")], copy_first=False)
report(r)
for dirpath, _, files in os.walk(os.path.join(r.folder_path, "content")):
    for n in files:
        print("  in the batch:", os.path.relpath(os.path.join(dirpath, n), r.folder_path))

# 6. The guard, on lines an agent might write.
show("6. The guard's judgement on compound lines (judge() executes nothing)")
lines = [
    "dz safedel --yes build/",
    "dz safedel --yes build/ | tail -1",
    "dz safedel --yes build/; git worktree prune",
    "dz safedel --yes build/ && rmdir out",
    "dz safedel build/",
    "rm -rf build && rm -rf dist",
    "git worktree remove ../proj__feature",
]
for scope in ("safedel", "full"):
    print("\n  scope = %s%s" % (scope, " (the default)" if scope == "safedel" else " (DZ_GUARD_SCOPE=full)"))
    for line in lines:
        v = judge(line, "bash", {"scope": scope})
        print("   %-6s %-4s %-45s %s" % (v.decision, v.rule or "", line, (v.why or "")[:70]))

print("\n(scratch left at %s for inspection)" % root)
