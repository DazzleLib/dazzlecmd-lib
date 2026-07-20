"""Tests for dazzlecmd_lib.shell_fix -- current-shell PATH healing.

The in-process mutation itself (running the emitted .cmd in a real cmd
session, dot-sourcing the .ps1 in real PowerShell) is checklist + live
territory; these tests pin script content, shell detection through the
process-table seam, invocation dialects, and the machine channel's
one-line purity.
"""

import os

import pytest

from dazzlecmd_lib import self_setup, shell_fix
from dazzlecmd_lib.shell_fix import (
    SHELL_BASH,
    SHELL_CMD,
    SHELL_POWERSHELL,
    _msys_form,
    all_invocation_lines,
    detect_invoking_shell,
    emit_current_shell_fix,
    invocation_line,
    write_fix_scripts,
)

SCRIPTS_DIR = r"C:\Users\u\AppData\Roaming\Python\Python313\Scripts"


class TestMsysForm:
    def test_drive_conversion(self):
        assert _msys_form(r"C:\a\b") == "/c/a/b"

    def test_non_drive_passthrough(self):
        assert _msys_form(r"\\server\share") == "//server/share"


class TestScriptEmission:
    def test_writes_three_scripts(self, tmp_path):
        paths = write_fix_scripts(SCRIPTS_DIR, brand="agg",
                                  dest_dir=str(tmp_path))
        files = sorted(p.name for p in tmp_path.iterdir())
        assert files == ["agg-path.cmd", "agg-path.ps1", "agg-path.sh"]
        assert paths[SHELL_CMD].endswith("agg-path.cmd")
        assert paths[SHELL_POWERSHELL].endswith("agg-path.ps1")
        assert paths[SHELL_BASH].endswith("agg-path.sh")

    def test_scripts_are_ascii_and_idempotent_by_construction(self, tmp_path):
        write_fix_scripts(SCRIPTS_DIR, brand="agg", dest_dir=str(tmp_path))
        for p in tmp_path.iterdir():
            content = p.read_bytes()
            content.decode("ascii")  # raises on any non-ASCII byte
            text = content.decode("ascii")
            # every dialect carries a membership check before appending
            assert ("findstr" in text) or ("-contains" in text) \
                or ("case \":$PATH:\"" in text)

    def test_ps1_and_sh_warn_about_sourcing(self, tmp_path):
        # The correction-as-test anchor: plain-running the .ps1/.sh does
        # NOT persist -- the scripts themselves must say so.
        write_fix_scripts(SCRIPTS_DIR, brand="agg", dest_dir=str(tmp_path))
        assert "DOT-SOURCE" in (tmp_path / "agg-path.ps1").read_text()
        assert "SOURCE" in (tmp_path / "agg-path.sh").read_text()

    def test_cmd_uses_crlf(self, tmp_path):
        write_fix_scripts(SCRIPTS_DIR, brand="agg", dest_dir=str(tmp_path))
        assert b"\r\n" in (tmp_path / "agg-path.cmd").read_bytes()

    def test_sh_uses_msys_dir_on_windows(self, tmp_path):
        write_fix_scripts(SCRIPTS_DIR, brand="agg", dest_dir=str(tmp_path))
        text = (tmp_path / "agg-path.sh").read_text()
        if os.name == "nt":
            assert "/c/Users/u/" in text
        else:
            assert SCRIPTS_DIR in text


class TestInvocationLines:
    def test_cmd_form_runs_in_process(self):
        line = invocation_line(SHELL_CMD, {}, brand="agg")
        assert line == '"%TEMP%\\agg-path.cmd"'
        assert not line.startswith(".")  # batch needs no sourcing

    def test_powershell_form_is_dot_sourced(self):
        line = invocation_line(SHELL_POWERSHELL, {}, brand="agg")
        assert line.startswith(". ")  # load-bearing: MUST dot-source

    def test_bash_form_is_sourced(self):
        line = invocation_line(SHELL_BASH,
                               {SHELL_BASH: "/tmp/agg-path.sh"},
                               brand="agg")
        assert line.startswith("source ")

    def test_all_lines_labeled(self):
        rows = all_invocation_lines({SHELL_BASH: "/tmp/agg-path.sh"},
                                    brand="agg")
        labels = [r[0] for r in rows]
        assert SHELL_CMD in labels and SHELL_POWERSHELL in labels


class TestDetection:
    def test_windows_parent_walk_finds_shell(self, monkeypatch):
        monkeypatch.setattr(shell_fix.os, "name", "nt")
        monkeypatch.delenv("MSYSTEM", raising=False)
        me = os.getpid()
        table = {me: (100, "python.exe"),
                 100: (50, "powershell.exe"),
                 50: (1, "explorer.exe")}
        monkeypatch.setattr(shell_fix, "_windows_process_table",
                            lambda: table)
        assert detect_invoking_shell() == SHELL_POWERSHELL

    def test_windows_walk_stops_at_max_hops(self, monkeypatch):
        monkeypatch.setattr(shell_fix.os, "name", "nt")
        monkeypatch.delenv("MSYSTEM", raising=False)
        me = os.getpid()
        table = {me: (me, "python.exe")}  # self-loop, no shell
        monkeypatch.setattr(shell_fix, "_windows_process_table",
                            lambda: table)
        assert detect_invoking_shell() is None

    def test_msystem_fingerprint_wins(self, monkeypatch):
        monkeypatch.setattr(shell_fix.os, "name", "nt")
        monkeypatch.setenv("MSYSTEM", "MINGW64")
        monkeypatch.setenv("SHELL", "/usr/bin/bash")
        assert detect_invoking_shell() == SHELL_BASH

    def test_posix_shell_env(self, monkeypatch):
        monkeypatch.setattr(shell_fix.os, "name", "posix")
        monkeypatch.setenv("SHELL", "/usr/bin/zsh")
        assert detect_invoking_shell() == "zsh"


class TestEmitCurrentShellFix:
    def test_detected_shell_prints_one_invocation(self, tmp_path,
                                                  monkeypatch):
        monkeypatch.setattr(shell_fix, "_temp_dir", lambda: str(tmp_path))
        monkeypatch.setattr(shell_fix, "detect_invoking_shell",
                            lambda: SHELL_CMD)
        monkeypatch.setattr(shell_fix, "load_clipboard", lambda t: True)
        out = []
        emit_current_shell_fix(SCRIPTS_DIR, brand="agg",
                               print_fn=out.append)
        joined = "\n".join(out)
        assert "agg-path.cmd" in joined
        assert "clipboard" in joined
        assert "agg-path.ps1" not in joined  # one dialect only

    def test_unknown_shell_prints_all_labeled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(shell_fix, "_temp_dir", lambda: str(tmp_path))
        monkeypatch.setattr(shell_fix, "detect_invoking_shell",
                            lambda: None)
        out = []
        emit_current_shell_fix(SCRIPTS_DIR, brand="agg",
                               print_fn=out.append, clipboard=False)
        joined = "\n".join(out)
        assert "agg-path.cmd" in joined and "agg-path.ps1" in joined


class TestRunSelfSetupIntegration:
    def _rig_stale_shell(self, tmp_path, monkeypatch):
        """Healthy-persisted, stale-effective (needs_new_terminal)."""
        monkeypatch.setattr(self_setup, "scripts_dir_for",
                            lambda scheme: str(tmp_path))
        (tmp_path / "agg.exe").write_text("")
        plain = tmp_path / "agg"
        plain.write_text("")
        plain.chmod(0o755)
        monkeypatch.setenv("PATH", r"C:\Windows" if os.name == "nt"
                           else "/usr/bin")
        monkeypatch.setattr(self_setup, "persisted_user_path",
                            lambda: str(tmp_path))
        monkeypatch.setattr(self_setup.os, "name", "nt")

    def test_stale_shell_gets_fix_instructions(self, tmp_path, monkeypatch):
        self._rig_stale_shell(tmp_path, monkeypatch)
        monkeypatch.setattr(shell_fix, "_temp_dir", lambda: str(tmp_path))
        monkeypatch.setattr(shell_fix, "detect_invoking_shell",
                            lambda: SHELL_CMD)
        monkeypatch.setattr(shell_fix, "load_clipboard", lambda t: False)
        out = []
        rc = self_setup.run_self_setup(["agg"], print_fn=out.append)
        assert rc == 0
        assert any("agg-path.cmd" in ln for ln in out)

    def test_emit_shell_fix_stdout_is_exactly_one_line(self, tmp_path,
                                                       monkeypatch):
        self._rig_stale_shell(tmp_path, monkeypatch)
        monkeypatch.setattr(shell_fix, "_temp_dir", lambda: str(tmp_path))
        monkeypatch.setattr(shell_fix, "detect_invoking_shell",
                            lambda: SHELL_POWERSHELL)
        out = []
        rc = self_setup.run_self_setup(["agg"], emit_shell_fix=True,
                                       print_fn=out.append)
        assert rc == 0
        assert len(out) == 1  # machine channel purity
        assert out[0].startswith(". ")  # the dot-source form
