"""Red-green audit for the safedel hotfix (DazzleLib/dazzlecmd-lib#7).

For each named revert: back up the two source files, neutralise one behaviour
(keeping every signature), run only tests/test_core_safedel_hotfix.py, record
which tests fail, then restore byte-for-byte and verify by hash.

Reverts:
  C1     lexists refusal in _stage_regular           -> 'if False and ...'
  C2     non-EXDEV rename refuses (no copy fallback)  -> 'if False:'
  C1+C2  both: the shipped 0.10.35 behaviour (copy fallback overwrites)
  C3     partial cross-device copy cleanup            -> 'leftover = None'
  C4     literal paths in classify                    -> 'if False:'

Safety: every test runs under tmp_path; the literal-~ trash test guards all
path-touching steps against the real home folder, so the C4 revert raises
there instead of touching it. Restores run in a finally block.
"""

import hashlib
import os
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PLATFORM = os.path.join(ROOT, "dazzlecmd_lib", "core", "safedel", "_platform.py")
CLASSIFIER = os.path.join(ROOT, "dazzlecmd_lib", "core", "safedel", "_classifier.py")
TESTS = os.path.join(ROOT, "tests", "test_core_safedel_hotfix.py")

EDITS = {
    "C1": [(PLATFORM, "    if os.path.lexists(dest_path):\n",
            "    if False and os.path.lexists(dest_path):\n")],
    "C2": [(PLATFORM, "        if e.errno != errno.EXDEV:\n            return StagingResult(",
            "        if False:\n            return StagingResult(")],
    "C3": [(PLATFORM, "        leftover = _discard_partial_copy(dest_path)\n",
            "        leftover = None\n")],
    "C4": [(CLASSIFIER, "    if _would_expand(path):\n", "    if False:\n")],
}
EDITS["C1+C2"] = EDITS["C1"] + EDITS["C2"]
ORDER = ["C1", "C2", "C1+C2", "C3", "C4"]


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


SELECT = []  # e.g. ["-k", "literal_tilde", "--tb=short"] via argv after the revert names


def run_tests():
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", TESTS, "-q", "-p", "no:cacheprovider",
         "--tb=line", "-rf"] + SELECT,
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    failed = [l.split(" ", 1)[1].split(" - ")[0] for l in proc.stdout.splitlines()
              if l.startswith("FAILED ")]
    reasons = [l for l in proc.stdout.splitlines() if l.startswith("FAILED ")]
    summary = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    if SELECT:
        reasons = proc.stdout.splitlines()
    return failed, reasons, summary


def main():
    # usage: redgreen_safedel_hotfix.py [REVERT ...] [-- pytest args]
    global ORDER, SELECT
    argv = sys.argv[1:]
    if "--" in argv:
        i = argv.index("--")
        argv, SELECT = argv[:i], argv[i + 1:]
    if argv:
        ORDER = argv
    files = [PLATFORM, CLASSIFIER]
    backup = {p: open(p, "rb").read() for p in files}
    before = {p: sha(p) for p in files}
    results = {}
    try:
        for name in ORDER:
            for path, old, new in EDITS[name]:
                text = open(path, encoding="utf-8").read()
                assert text.count(old) == 1, f"{name}: anchor not unique in {path}"
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(text.replace(old, new))
            failed, reasons, summary = run_tests()
            results[name] = (failed, reasons, summary)
            for p in files:
                with open(p, "wb") as f:
                    f.write(backup[p])
    finally:
        for p in files:
            with open(p, "wb") as f:
                f.write(backup[p])
    for p in files:
        assert sha(p) == before[p], f"restore failed for {p}"

    for name in ORDER:
        failed, reasons, summary = results[name]
        print(f"\n== revert {name}: {summary}")
        for r in reasons:
            print("   " + r[:220])
    green = run_tests()
    print(f"\n== restored: {green[2]}  (source hashes verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
