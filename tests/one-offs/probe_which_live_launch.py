"""Live launch probe for the 0.10.34-alpha Windows executable-lookup fix.

Builds npm's cmd-shim layout -- a POSIX ``tool`` script beside ``tool.cmd`` --
in a temporary folder, puts ONLY that folder on PATH, resolves ``tool`` with the
library's ``which_with_pathext`` and with ``shutil.which``, then LAUNCHES each
result. That is the claim the unit tests (pure, synthetic PATHs) cannot make:
Windows will actually run what the lookup returns.

Windows only. Harmless: the .cmd echoes a marker; the extensionless sh script
is not something CreateProcess can run (that refusal is the bug's symptom).

Run:  python tests/one-offs/probe_which_live_launch.py
Exit: 0 when the library's answer is tool.cmd AND launching it prints the marker.
"""
import os
import platform
import shutil
import subprocess
import sys
import tempfile

from dazzlecmd_lib.paths import which_with_pathext

MARKER = "CMD-SHIM-RAN"


def launch(path):
    """Launch `path` with no shell; return a one-line outcome."""
    try:
        r = subprocess.run([path], capture_output=True, text=True, timeout=20)
        out = (r.stdout or "").strip().replace("\n", " | ")
        return f"rc={r.returncode} stdout={out!r}", MARKER in (r.stdout or "")
    except OSError as exc:
        return f"OSError: {exc}", False


def main():
    if os.name != "nt":
        print("SKIP: Windows-only probe")
        return 0
    print(f"python {platform.python_version()} on {platform.platform()}")
    with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as cwd:
        with open(os.path.join(d, "tool"), "w", newline="\n") as f:
            f.write("#!/bin/sh\necho posix-script\n")
        with open(os.path.join(d, "tool.cmd"), "w", newline="\r\n") as f:
            f.write(f"@echo off\necho {MARKER}\n")
        saved = {k: os.environ.get(k) for k in ("PATH", "PATHEXT")}
        here = os.getcwd()
        try:
            os.environ["PATH"] = d                      # only the shim folder
            os.environ["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"
            os.chdir(cwd)                               # an empty cwd: no cwd effects
            lib = which_with_pathext("tool")
            std = shutil.which("tool")
        finally:
            os.chdir(here)
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        lib_line, lib_ok = launch(lib) if lib else ("(not found)", False)
        std_line, _ = launch(std) if std else ("(not found)", False)
        print(f"library    which_with_pathext -> {lib}\n             launch: {lib_line}")
        print(f"stdlib     shutil.which       -> {std}\n             launch: {std_line}")
        ok = bool(lib) and lib.lower().endswith("tool.cmd") and lib_ok
        print("RESULT:", "PASS -- Windows runs what the library returns" if ok else "FAIL")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
