"""Run the prototype's transcript measurement against the LIBRARY's guard (goal slice G4).

READ-ONLY, like the script it wraps: transcript files are opened for reading and each command
string is fed to the pure judge; nothing is executed. The library's
`dazzlecmd_lib/core/safedel/_guard.py` is loaded by path and registered as `_guard`, so the
prototype script's `import _guard` picks it up instead of the prototype copy beside it.

    python measure_guard_transcripts.py [N_FILES] [--samples K] [--dump DIR]
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB_GUARD = os.path.join(HERE, os.pardir, os.pardir, "dazzlecmd_lib", "core", "safedel", "_guard.py")
SCRIPT = os.path.join(HERE, "thinking", "safedel-line-guard", "registry-v1", "measure_transcripts.py")

spec = importlib.util.spec_from_file_location("_guard", LIB_GUARD)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
sys.modules["_guard"] = guard
# DZ_GUARD_SCOPE=safedel measures what the installed hook will refuse (its default scope);
# without it, judge()'s own default -- the whole table -- is measured.
if os.environ.get("DZ_GUARD_SCOPE"):
    guard.DEFAULT_CTX["scope"] = guard._scope_from_env(os.environ)

spec2 = importlib.util.spec_from_file_location("measure_transcripts", SCRIPT)
m = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(m)

if __name__ == "__main__":
    print("guard under test:", os.path.normpath(LIB_GUARD))
    m.main(sys.argv[1:])
