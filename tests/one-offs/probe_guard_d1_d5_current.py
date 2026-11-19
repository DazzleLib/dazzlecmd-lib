"""How does the library's _guard judge each D1-D5 case today? Text only; runs nothing.

D1 bare `dz safedel` without --yes; D2 interpreter one-liners that delete;
D3 deletes in temp folders, alone and chained; D4 discarded output / `&`;
D5 git checkout -- / git restore / Remove-Item in a chain / --force-with-lease.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, os.pardir, os.pardir, "dazzlecmd_lib", "core", "safedel", "_guard.py")
spec = importlib.util.spec_from_file_location("_guard_probe", PATH)
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)

CASES = [
    ("D1", "bash", "dz safedel build"),
    ("D1", "bash", "dz safedel --yes build"),
    ("D2", "bash", 'python -c "import shutil; shutil.rmtree(\'build\')"'),
    ("D2", "bash", "python - <<'PY'\nimport os\nos.remove('a.db')\nPY"),
    ("D3", "bash", "rm -rf /tmp/scratch-1/out"),
    ("D3", "bash", "rm -rf /tmp/a && rm -rf /tmp/b"),
    ("D4", "bash", "rm -rf build 2>/dev/null"),
    ("D4", "bash", "rm -rf node_modules &"),
    ("D5", "bash", "git checkout -- src/a.py"),
    ("D5", "bash", "git restore src/a.py"),
    ("D5", "bash", "cd /c/x && git restore src/a.py"),
    ("D5", "bash", "git status && git checkout -- src/a.py"),
    ("D5", "pwsh", "Get-ChildItem x; Remove-Item -Recurse x"),
    ("D5", "pwsh", "Remove-Item -Recurse x"),
    ("D5", "bash", "git push --force-with-lease"),
    ("D5", "bash", "git commit --amend --no-edit && git push --force-with-lease"),
    ("--", "bash", "git status"),
]


def main():
    for d, shell, line in CASES:
        v = G.judge(line, shell)
        decision = getattr(v, "decision", v)
        reason = ""
        for attr in ("rule", "reason", "form", "message"):
            val = getattr(v, attr, None)
            if val:
                reason += "%s=%s " % (attr, str(val).splitlines()[0][:70])
        print("%-3s %-10s %-58r -> %s  %s" % (d, shell, line[:58], decision, reason))
    return 0


if __name__ == "__main__":
    sys.exit(main())
