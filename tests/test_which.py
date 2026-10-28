"""The conformance table for the `which_*` family: what CreateProcess runs.

Every row builds a PATH layout in tmp_path and asserts which file wins. The
rows are data on purpose: claude-session-backup's `claude_exe` and
dcc-patcher's `resolve.candidates` implement the same semantics, and this
table is what the copies are checked against so they cannot drift.

The Windows walk is exercised on every OS: `windows_candidates` takes PATH and
PATHEXT explicitly, and the public wrappers branch on `paths._IS_WINDOWS`,
which the tests set. The semantics depend only on PATH, PATHEXT and the files.
"""
from __future__ import annotations

import os

import pytest

from dazzlecmd_lib import paths
from dazzlecmd_lib.paths import (
    RUNNABLE_EXTS,
    which_all_on_path,
    which_in_dir,
    which_with_pathext,
    windows_candidates,
)

# A realistic PATHEXT: the runnable four plus the non-runnable extras a dev
# box accumulates after them.
PATHEXT = ".COM;.EXE;.BAT;.CMD;.VBS;.JS;.PY"

_CONTENT = {
    "": b"#!/bin/sh\nexit 0\n",           # npm cmd-shim style POSIX launcher
    ".cmd": b"@echo off\r\nexit /b 0\r\n",
    ".bat": b"@echo off\r\nexit /b 0\r\n",
    ".exe": b"MZ",
    ".com": b"MZ",
    ".ps1": b"exit 0\n",
    ".py": b"raise SystemExit\n",
}


def _layout(tmp_path, folders):
    """folders: [(folder_name, [file_name, ...]), ...] -> {folder_name: path}."""
    made = {}
    for folder, files in folders:
        d = tmp_path / folder
        d.mkdir(exist_ok=True)
        for f in files:
            (d / f).write_bytes(_CONTENT[os.path.splitext(f)[1].lower()])
        made[folder] = d
    return made


def _same(a, b):
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


@pytest.fixture
def windows(monkeypatch):
    """Make the public wrappers take their Windows branch on any host."""
    monkeypatch.setattr(paths, "_IS_WINDOWS", True)


# -- the table --------------------------------------------------------------
#
# id, PATH folders in order (name -> files), PATHEXT, lookup name, winner
# ("folder/file") or None.

CONFORMANCE = [
    ("sh-beside-cmd-in-first-folder",
     [("d1", ["tool", "tool.cmd"]), ("d2", ["tool.exe"])], PATHEXT, "tool", "d1/tool.cmd"),
    ("exe-in-earlier-folder-beats-later-cmd",
     [("d1", ["tool.exe"]), ("d2", ["tool", "tool.cmd"])], PATHEXT, "tool", "d1/tool.exe"),
    ("exe-beats-cmd-in-the-same-folder",
     [("d1", ["tool.exe", "tool.cmd"])], PATHEXT, "tool", "d1/tool.exe"),
    ("sh-script-alone-is-not-runnable",
     [("d1", ["tool"])], PATHEXT, "tool", None),
    ("npm-layout-with-ps1-listed-first-in-pathext",
     [("d1", ["tool", "tool.cmd", "tool.ps1"])], ".PS1;.CMD;.EXE", "tool", "d1/tool.cmd"),
    ("pathext-unset-uses-the-cmd-default",
     [("d1", ["tool.cmd"])], None, "tool", "d1/tool.cmd"),
    ("pathext-with-nothing-runnable-uses-the-default",
     [("d1", ["tool.cmd", "tool.py"])], ".PY;.JS", "tool", "d1/tool.cmd"),
    ("name-given-with-extension-is-tried-as-given",
     [("d1", ["tool.cmd", "tool.exe"])], PATHEXT, "tool.cmd", "d1/tool.cmd"),
    ("name-with-uppercase-extension-is-tried-as-given",
     [("d1", ["tool.CMD"])], PATHEXT, "tool.CMD", "d1/tool.CMD"),
    ("pathext-order-within-a-folder-is-honoured",
     [("d1", ["tool.exe", "tool.cmd"])], ".CMD;.EXE", "tool", "d1/tool.cmd"),
    ("folder-order-beats-extension-order",
     [("d1", ["tool.bat"]), ("d2", ["tool.exe"])], PATHEXT, "tool", "d1/tool.bat"),
]


@pytest.mark.parametrize("case_id,folders,pathext,name,winner", CONFORMANCE,
                         ids=[c[0] for c in CONFORMANCE])
def test_conformance_table(tmp_path, case_id, folders, pathext, name, winner):
    made = _layout(tmp_path, folders)
    path = ";".join(str(made[f]) for f, _ in folders)
    hits = windows_candidates(name, path, pathext)
    if winner is None:
        assert hits == []
    else:
        folder, file = winner.split("/")
        assert hits and _same(hits[0], made[folder] / file), hits


# -- the two defects this change fixes (anchors) ---------------------------

def test_which_in_dir_ignores_a_cwd_file(tmp_path, monkeypatch, windows):
    """which_in_dir("tool", <empty dir>) with tool.cmd in the CURRENT directory
    is None -- the docstring says ONE directory, and shutil.which's Windows
    curdir-first rule had made it search the cwd as well."""
    made = _layout(tmp_path, [("cwd", ["tool.cmd"]), ("empty", [])])
    monkeypatch.chdir(made["cwd"])
    monkeypatch.setenv("PATHEXT", PATHEXT)
    # Keep the curdir defect LIVE: shutil.which skips the cwd when this is set,
    # which would make the test pass against the old code on such a host.
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    assert which_in_dir("tool", str(made["empty"])) is None


def test_which_all_on_path_never_lists_the_cwd(tmp_path, monkeypatch, windows):
    """A tool.cmd in the cwd used to appear once PER PATH ENTRY in the shadow
    report; it is not a PATH entry and is not listed at all."""
    made = _layout(tmp_path, [("cwd", ["tool.cmd"]), ("d1", ["tool.exe"]), ("d2", ["other.exe"])])
    monkeypatch.chdir(made["cwd"])
    monkeypatch.setenv("PATH", f"{made['d1']};{made['d2']}")
    monkeypatch.setenv("PATHEXT", PATHEXT)
    # As above: keep the curdir defect live regardless of the host's setting.
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    hits = which_all_on_path("tool")
    assert len(hits) == 1 and _same(hits[0], made["d1"] / "tool.exe"), hits


# -- the public wrappers on their Windows branch ------------------------------

def test_which_with_pathext_picks_the_cmd_not_the_script(tmp_path, monkeypatch, windows):
    made = _layout(tmp_path, [("d1", ["tool", "tool.cmd"])])
    monkeypatch.setenv("PATH", str(made["d1"]))
    monkeypatch.setenv("PATHEXT", PATHEXT)
    got = which_with_pathext("tool")
    assert got is not None and _same(got, made["d1"] / "tool.cmd")


def test_which_with_pathext_honours_the_environments_pathext_order(tmp_path, monkeypatch, windows):
    """m10 (mutation sweep survivor): the wrapper reads PATHEXT from the
    environment and keeps ITS order -- it does not substitute the default."""
    made = _layout(tmp_path, [("d1", ["tool.exe", "tool.cmd"])])
    monkeypatch.setenv("PATH", str(made["d1"]))
    monkeypatch.setenv("PATHEXT", ".CMD;.EXE")
    got = which_with_pathext("tool")
    assert got is not None and _same(got, made["d1"] / "tool.cmd")


def test_which_all_on_path_is_one_hit_per_folder(tmp_path, monkeypatch, windows):
    made = _layout(tmp_path, [("d1", ["tool.exe", "tool.cmd"]), ("d2", ["tool", "tool.cmd"])])
    monkeypatch.setenv("PATH", f"{made['d1']};{made['d2']}")
    monkeypatch.setenv("PATHEXT", PATHEXT)
    hits = which_all_on_path("tool")
    assert [os.path.basename(h).lower() for h in hits] == ["tool.exe", "tool.cmd"]


def test_which_in_dir_finds_the_runnable_file(tmp_path, monkeypatch, windows):
    made = _layout(tmp_path, [("d1", ["tool", "tool.exe"])])
    monkeypatch.setenv("PATHEXT", PATHEXT)
    got = which_in_dir("tool", str(made["d1"]))
    assert got is not None and _same(got, made["d1"] / "tool.exe")


# -- PATH entry hygiene --------------------------------------------------------

def test_quoted_entries_are_unquoted(tmp_path):
    made = _layout(tmp_path, [("d1", ["tool.cmd"])])
    hits = windows_candidates("tool", f'"{made["d1"]}"', PATHEXT)
    assert hits and _same(hits[0], made["d1"] / "tool.cmd")


def test_percent_vars_are_expanded(tmp_path, monkeypatch):
    made = _layout(tmp_path, [("d1", ["tool.cmd"])])
    monkeypatch.setenv("DCL_TEST_BIN", str(made["d1"]))
    hits = windows_candidates("tool", "%DCL_TEST_BIN%", PATHEXT)
    assert hits and _same(hits[0], made["d1"] / "tool.cmd")


def test_unknown_percent_var_is_left_as_written(tmp_path, monkeypatch):
    monkeypatch.delenv("DCL_NOPE", raising=False)
    made = _layout(tmp_path, [("%DCL_NOPE%", ["tool.cmd"])])
    hits = windows_candidates("tool", str(made["%DCL_NOPE%"]), PATHEXT)
    assert hits and _same(hits[0], made["%DCL_NOPE%"] / "tool.cmd")


def test_empty_entries_are_skipped_and_duplicates_dropped(tmp_path):
    made = _layout(tmp_path, [("d1", ["tool.cmd"])])
    d1 = str(made["d1"])
    hits = windows_candidates("tool", f";  ;{d1};{d1};", PATHEXT)
    assert len(hits) == 1


def test_name_with_runnable_extension_is_tried_once(tmp_path):
    made = _layout(tmp_path, [("d1", ["tool.cmd", "tool.cmd.exe"])])
    hits = windows_candidates("tool.cmd", str(made["d1"]), PATHEXT)
    assert [os.path.basename(h).lower() for h in hits] == ["tool.cmd"]


# -- POSIX branches are shutil.which, unchanged ---------------------------------

def test_posix_wrappers_delegate_to_shutil_which(monkeypatch):
    monkeypatch.setattr(paths, "_IS_WINDOWS", False)
    calls = []

    def fake_which(name, path=None):
        calls.append((name, path))
        return "/usr/local/bin/" + name

    monkeypatch.setattr(paths.shutil, "which", fake_which)
    # The POSIX branch splits PATH on the HOST's os.pathsep (correct on a
    # POSIX host; this test also runs on Windows, where it is ';').
    monkeypatch.setenv("PATH", os.pathsep.join(["/a", "/b"]))
    assert which_with_pathext("tool") == "/usr/local/bin/tool"
    assert which_in_dir("tool", "/x") == "/usr/local/bin/tool"
    assert which_all_on_path("tool") == ["/usr/local/bin/tool", "/usr/local/bin/tool"]
    assert calls == [("tool", None), ("tool", "/x"), ("tool", "/a"), ("tool", "/b")]


def test_empty_inputs():
    assert which_with_pathext("") is None
    assert which_in_dir("", "x") is None and which_in_dir("tool", "") is None
    assert which_all_on_path("") == []


def test_runnable_set_is_cmd_exe_default_order():
    assert RUNNABLE_EXTS == (".com", ".exe", ".bat", ".cmd")
