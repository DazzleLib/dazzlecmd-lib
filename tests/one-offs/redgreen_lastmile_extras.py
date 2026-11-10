"""Red-green audit for the tests added during the safedel hotfix last mile.

Reverts (each restored byte-for-byte, verified by hash):
  PKG   remove the "templates/**/.github/**/*" package-data pattern
        -> tests/test_package_data.py should go red
  BLUE  drop `BLUE = "\\033[34m"` from dazzlecmd_lib/colors.py (source tree)
        -> dazzlecmd tests/test_colors.py, run against the source lib
  STUB  make pretend_os_name a no-op
        -> on Windows the pretend-POSIX tests should go red; on Linux the
           pretend-Windows ones (run separately via the WSL runner)

Usage: python redgreen_lastmile_extras.py [PKG] [BLUE] [STUB]
"""
import hashlib
import os
import subprocess
import sys

LIB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DZ = r"C:\code\dazzlecmd\github"

EDITS = {
    "PKG": (os.path.join(LIB, "pyproject.toml"),
            '    "templates/**/.github/**/*",\n', ""),
    "BLUE": (os.path.join(LIB, "dazzlecmd_lib", "colors.py"),
             'BLUE = "\\033[34m"\n', ""),
    "STUB": (os.path.join(LIB, "tests", "_platform_stub.py"),
             '    for module in modules:\n        monkeypatch.setattr(module, "os", OsAs(name))\n',
             '    return  # REDGREEN-NOOP\n'),
}
RUNS = {
    "PKG": (LIB, [sys.executable, "-m", "pytest", "tests/test_package_data.py"], None),
    "BLUE": (DZ, [sys.executable, "-m", "pytest", "tests/test_colors.py"], LIB),
    "STUB": (LIB, [sys.executable, "-m", "pytest", "tests/test_self_setup.py",
                   "tests/test_shell_fix.py"], None),
}


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def run(name):
    cwd, cmd, pypath = RUNS[name]
    env = dict(os.environ)
    if pypath:
        env["PYTHONPATH"] = pypath
    p = subprocess.run(cmd + ["-q", "-p", "no:cacheprovider", "--tb=line", "-rf"],
                       cwd=cwd, env=env, capture_output=True, text=True, timeout=300)
    lines = p.stdout.splitlines()
    return [l for l in lines if l.startswith("FAILED") or l.startswith("E ")], \
        (lines[-1] if lines else "")


def main():
    names = sys.argv[1:] or ["PKG", "BLUE", "STUB"]
    for name in names:
        path, old, new = EDITS[name]
        raw = open(path, "rb").read()
        before = sha(path)
        text = raw.decode("utf-8")
        nl = "\r\n" if "\r\n" in text else "\n"
        o, n = old.replace("\n", nl), new.replace("\n", nl)
        assert text.count(o) == 1, f"{name}: anchor not unique/absent in {path}"
        try:
            with open(path, "wb") as f:
                f.write(text.replace(o, n).encode("utf-8"))
            failed, summary = run(name)
        finally:
            with open(path, "wb") as f:
                f.write(raw)
        assert sha(path) == before, f"restore failed: {path}"
        print(f"\n== revert {name}: {summary}")
        for l in failed:
            print("   " + l[:200])
        print(f"   restored by hash: {os.path.basename(path)}")
        _, green = run(name)
        print(f"   after restore: {green}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
