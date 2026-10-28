"""Cross-platform path helpers -- shared library for setup and runtime layers.

Covers the three recurring path concerns both layers face:

    1. Relative-path resolution against a tool's directory (runtime `script_path`
       and setup `steps` alike). The v0.7.18 shell_env fix was an instance of
       this; this module generalizes the pattern so every caller behaves
       consistently.

    2. Windows executable suffix handling. Authors often declare binaries
       without extensions; on Windows we append `.exe` (or consult PATHEXT)
       for display and dispatch.

    3. WSL path translation. A tool running under WSL may need to pass paths
       to or from a Windows-native process. This module provides the common
       `/mnt/c/foo <-> C:\\foo` translation without attempting to handle
       every edge case (UNC paths, symlinks, etc.).

Design: every helper is a pure function taking explicit inputs. No global
state, no caching, no environment mutation. Callers compose as needed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from typing import Optional


def resolve_relative_path(candidate: str, tool_dir: str) -> str:
    """Resolve a relative path against a tool's directory when doing so yields
    an existing file.

    Absolute paths pass through unchanged. Paths beginning with a shell
    variable expansion marker (`$VAR`, `%VAR%`) pass through unchanged -- the
    shell owns that resolution.

    Otherwise, join `candidate` against `tool_dir`; if the joined path points
    to an existing file, return it. Else return the original `candidate`
    (caller decides how to proceed -- typically passes to the shell/subprocess
    for its own resolution against cwd).

    This is the v0.7.18 shell_env.script fix, generalized. Every caller that
    accepts a relative path from a manifest field should use this helper to
    keep behavior consistent across runners.
    """
    if not candidate:
        return candidate
    if os.path.isabs(candidate):
        return candidate
    if candidate.startswith("$") or candidate.startswith("%"):
        return candidate
    joined = os.path.join(tool_dir, candidate)
    if os.path.isfile(joined):
        return joined
    return candidate


def ensure_windows_executable_suffix(name: str) -> str:
    """On Windows, append `.exe` to names that have no extension.

    Returns the original `name` unchanged on non-Windows hosts, on empty input,
    or when `name` already has an extension (trusted as-is).

    Intended for display / logging / manifest normalization. For actual
    executable lookup use `which_with_pathext`, which walks PATH x PATHEXT the
    way CreateProcess resolves a name (see the executable-lookup section).
    """
    if os.name != "nt" or not name:
        return name
    _, ext = os.path.splitext(name)
    if ext:
        return name
    return name + ".exe"


def translate_wsl_path(path: str, direction: str) -> str:
    """Translate between WSL (/mnt/<drive>/...) and Windows (<drive>:\\...) forms.

    Args:
        path: Input path.
        direction: "to_windows" converts `/mnt/c/foo` -> `C:\\foo`.
                   "to_wsl" converts `C:\\foo` -> `/mnt/c/foo`.

    Paths that do not match the expected source form pass through unchanged.
    UNC paths, drive-relative paths (e.g., `C:foo` without a separator), and
    paths with unusual drive letters are not handled -- this helper is for
    the common `/mnt/<single-letter>/...` and `<letter>:...` cases.

    Raises:
        ValueError: if `direction` is neither "to_windows" nor "to_wsl".
    """
    if direction == "to_windows":
        if path.startswith("/mnt/") and len(path) >= 7 and path[6] == "/":
            drive = path[5].upper()
            rest = path[7:].replace("/", "\\")
            return f"{drive}:\\{rest}"
        return path
    if direction == "to_wsl":
        if len(path) >= 3 and path[1] == ":" and path[2] in ("\\", "/"):
            drive = path[0].lower()
            rest = path[3:].replace("\\", "/")
            return f"/mnt/{drive}/{rest}"
        return path
    raise ValueError(
        f"direction must be 'to_windows' or 'to_wsl', got {direction!r}"
    )


# -- Executable lookup: what the EXECUTOR will run, not what the shell shows --
#
# On POSIX the executor is execvp and `shutil.which` mirrors it exactly. On
# Windows the executor is CreateProcess, which runs PE images and -- given a
# full path -- .bat/.cmd files, and nothing else; cmd.exe's PATH x PATHEXT walk
# decides which file that is. `shutil.which` only approximates that walk, and
# on Python 3.12.0 (CPython gh-109590) it checks the bare name FIRST: with a
# POSIX `tool` script beside `tool.cmd` -- npm's cmd-shim layout, and any
# install that ships a sh launcher next to a .cmd one -- it returns the script,
# which CreateProcess refuses (WinError 193). On every Python it also consults
# the current directory first, as cmd.exe does, which `which_in_dir` was never
# meant to do. So on Windows these helpers walk PATH themselves:
#
#   * folders in PATH order; within a folder, PATHEXT order;
#   * PATHEXT intersected with what CreateProcess can run (RUNNABLE_EXTS),
#     keeping PATHEXT's order -- a .ps1 or .py is never returned even when
#     PATHEXT lists it first;
#   * never the bare name, unless the name already carries a runnable
#     extension (then it is tried as given, once);
#   * never the current directory;
#   * entries unquoted, %VAR%-expanded, empty ones skipped, duplicates dropped
#     by case-folded path.
#
# The same rules, and the same conformance table (tests/test_which.py), are
# carried by claude-session-backup's `claude_exe` module until it depends on
# this library, and by dcc-patcher's `resolve.candidates`.

#: What CreateProcess can run, in cmd.exe's default PATHEXT order. Also the
#: fallback when PATHEXT is unset or names nothing runnable.
RUNNABLE_EXTS = (".com", ".exe", ".bat", ".cmd")

_IS_WINDOWS = os.name == "nt"
_WIN_PATHSEP = ";"
_PERCENT_VAR = re.compile(r"%([^%]+)%")


def _expand_percent_vars(entry: str, env) -> str:
    """``%VAR%`` -> its value; an unknown ``%VAR%`` is left as written.
    (`os.path.expandvars` does this only on Windows hosts; doing it here keeps
    the walk identical when the tests run on a POSIX CI runner.)"""
    return _PERCENT_VAR.sub(lambda m: env.get(m.group(1), m.group(0)), entry)


def _runnable_extensions(name: str, pathext: Optional[str]) -> "list[str]":
    """The extensions to try for `name`, in order: a name that already carries
    a runnable extension is tried as given, once; otherwise PATHEXT's order
    restricted to RUNNABLE_EXTS, or RUNNABLE_EXTS itself when PATHEXT is unset
    or lists nothing CreateProcess can run."""
    if os.path.splitext(name)[1].lower() in RUNNABLE_EXTS:
        return [""]
    listed = [e.strip().lower() for e in (pathext or "").split(_WIN_PATHSEP) if e.strip()]
    runnable = [e for e in listed if e in RUNNABLE_EXTS]
    return runnable or list(RUNNABLE_EXTS)


def windows_candidates(name: str, path_value: str, pathext: Optional[str] = None,
                       *, first_per_folder: bool = False, env=None) -> "list[str]":
    """Every file CreateProcess could run for `name`, in the order cmd.exe
    would try them. `path_value` and `pathext` are raw environment values;
    `pathext=None` means "use the default runnable set". With
    `first_per_folder`, at most one hit per PATH folder (the shadow report's
    unit is the folder, not the file). Pure: never reads PATH, never the cwd."""
    env = os.environ if env is None else env
    exts = _runnable_extensions(name, pathext)
    found: "list[str]" = []
    seen: "set[str]" = set()
    for raw in path_value.split(_WIN_PATHSEP):
        folder = _expand_percent_vars(raw.strip().strip('"'), env)
        if not folder:
            continue
        for ext in exts:
            candidate = os.path.join(folder, name + ext)
            key = os.path.normcase(candidate)
            if key in seen:
                continue
            if os.path.isfile(candidate):
                seen.add(key)
                found.append(candidate)
                if first_per_folder:
                    break
    return found


def which_with_pathext(name: str) -> Optional[str]:
    """Locate an executable on PATH. Returns full path or None.

    On Windows this is the file CreateProcess will run for `name` -- the
    PATH x PATHEXT walk described above, never an extensionless script and
    never the current directory. On POSIX hosts it is `shutil.which`: PATHEXT
    is ignored and the name is matched exactly, executable bit required.

    Exists as a named helper so caller sites read "we are locating an
    executable" rather than "we are probing a path."
    """
    if not name:
        return None
    if not _IS_WINDOWS:
        return shutil.which(name)
    hits = windows_candidates(name, os.environ.get("PATH", ""), os.environ.get("PATHEXT"))
    return hits[0] if hits else None


def which_in_dir(name: str, directory: str) -> Optional[str]:
    """Locate an executable `name` inside ONE directory. Full path or None.

    The single-directory sibling of `which_with_pathext`: same semantics,
    scoped to `directory` instead of the whole PATH -- and ONLY `directory`:
    the current directory is never consulted, whatever cmd.exe would do. Used
    by self-setup to verify an aggregator's launcher shims exist where pip
    put them (dazzlecmd#103).
    """
    if not name or not directory:
        return None
    if not _IS_WINDOWS:
        return shutil.which(name, path=directory)
    hits = windows_candidates(name, directory, os.environ.get("PATHEXT"))
    return hits[0] if hits else None


def which_all_on_path(name: str) -> "list[str]":
    """Every PATH directory's hit for executable `name`, in PATH order.

    `which_with_pathext` stops at the first match; this returns one hit per
    PATH entry that has the executable (the first runnable one, in PATHEXT
    order, on Windows). The current directory is not a PATH entry and is
    never listed. Used by self-setup to surface shadowing copies of a
    command (dazzlecmd#103).
    """
    if not name:
        return []
    if _IS_WINDOWS:
        return windows_candidates(name, os.environ.get("PATH", ""),
                                  os.environ.get("PATHEXT"), first_per_folder=True)
    hits = []
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d.strip():
            continue
        hit = shutil.which(name, path=d)
        if hit:
            hits.append(hit)
    return hits


# Link primitives relocated to the constitutional namespace
# ``dazzlecmd_lib.core.links`` in v0.8.0. Re-exported here for backward
# compatibility -- existing ``from dazzlecmd_lib.paths import is_linked_project``
# (mode.py, default_meta_commands.py, dazzlecmd/importer.py, wtf-windows) keep
# working, and import identity is preserved (the re-exported objects ARE the
# core.links functions). Removal of these re-exports is deferred to a future
# release with a deprecation note.
from dazzlecmd_lib.core.links import (  # noqa: E402,F401  (intentional re-export)
    create_link,
    get_link_target,
    is_linked_project,
    remove_link,
)
