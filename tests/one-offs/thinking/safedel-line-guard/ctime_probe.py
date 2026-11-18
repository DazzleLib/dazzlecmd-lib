import os, sys, time, shutil, tempfile, inspect
from dazzle_filekit.operations import copy_tree_preserving_links
from dazzle_filekit.metadata import collect_file_metadata, metadata_to_json
import dazzlecmd_lib.core.safedel._recover as R
apply_file_metadata = R.apply_file_metadata
base = tempfile.mkdtemp(prefix="ctime_probe_", dir=sys.argv[1])
src = os.path.join(base, "orig"); os.makedirs(os.path.join(src, "sub"))
f = os.path.join(src, "sub", "a.txt"); open(f, "w").write("x")
single = os.path.join(base, "one.txt"); open(single, "w").write("y")
old = time.time() - 86400*30
for p in (f, os.path.join(src, "sub"), src, single): os.utime(p, (old, old))
def t(p):
    s = os.stat(p); return (round(s.st_ctime, 1), round(s.st_mtime, 1))
before = {k: t(p) for k, p in dict(root=src, sub=os.path.join(src,"sub"), file=f, single=single).items()}
meta_root = metadata_to_json(collect_file_metadata(src)); meta_one = metadata_to_json(collect_file_metadata(single))
time.sleep(2.2)
# simulate recovery's copy path (same volume) exactly as _recover_entry does
dst = os.path.join(base, "recovered"); copy_tree_preserving_links(src, dst)
dst1 = os.path.join(base, "one_recovered.txt"); shutil.copy2(single, dst1)
for tgt, m in ((dst, meta_root), (dst1, meta_one)):
    try: apply_file_metadata(tgt, m)
    except Exception as e: print("apply raised:", type(e).__name__, e)
after = dict(root=t(dst), sub=t(os.path.join(dst,"sub")), file=t(os.path.join(dst,"sub","a.txt")), single=t(dst1))
for k in before:
    b, a = before[k], after[k]
    print(f"{k:7} ctime {'SAME' if b[0]==a[0] else 'CHANGED by %.0fs' % (a[0]-b[0])}   mtime {'SAME' if b[1]==a[1] else 'CHANGED by %.0fs' % (a[1]-b[1])}")
# control: rename preserves
ren = os.path.join(base, "renamed"); os.rename(src, ren)
print("control rename: file ctime", "SAME" if t(os.path.join(ren,"sub","a.txt"))==before["file"] else "CHANGED")
print("scratch:", base)
