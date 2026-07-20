"""Tests for dazzlecmd_lib.self_setup -- the aggregator PATH bootstrap.

The registry-facing pieces run everywhere: the module exposes seams
(_read_user_path_raw / _write_user_path_raw / _broadcast_environment_change)
that these tests monkeypatch, so the fix flow is exercised on POSIX CI
runners with Windows-shaped (";"-separated, %VAR%-bearing, REG_EXPAND_SZ)
values. Only the end-to-end registry write is Windows-only territory, and
that is the human checklist's job, not this file's.
"""

import os
import sys

import pytest

from dazzlecmd_lib import self_setup
from dazzlecmd_lib.self_setup import (
    FixResult,
    REG_EXPAND_SZ,
    REG_SZ,
    SelfSetupReport,
    advise_posix,
    find_shims,
    first_run_hint,
    fix_windows,
    path_contains,
    render_report,
    split_path_value,
)


def _make_shim(dirpath, name):
    """Create a launcher every host flavor finds.

    shutil.which (via paths.which_in_dir) needs `name.exe` on Windows
    (PATHEXT probing skips extensionless names) and an executable-bit
    `name` on POSIX -- write both so tests pass on either CI host.
    """
    (dirpath / (name + ".exe")).write_text("")
    plain = dirpath / name
    plain.write_text("")
    plain.chmod(0o755)


# ---------------------------------------------------------------------------
# path_contains / normalization
# ---------------------------------------------------------------------------


class TestPathContainsDelegation:
    """The comparators HOMED to dazzle_filekit.pathenv (v0.3.3); these
    adapters keep the windows-bool spelling for fix_windows and the seam
    tests. Full comparator coverage lives in filekit's test_pathenv.py --
    these three prove the delegation wiring end to end."""

    def test_windows_membership_casefold_and_var(self, monkeypatch):
        monkeypatch.setenv("SELF_SETUP_TEST_ROOT", r"C:\Users\someone")
        assert path_contains(r"%SELF_SETUP_TEST_ROOT%\Bin;C:\x",
                             r"c:\users\someone\bin", windows=True)

    def test_windows_split_semicolon_any_host(self):
        assert split_path_value(r"C:\a;C:\b;;C:\c", windows=True) == \
            [r"C:\a", r"C:\b", r"C:\c"]

    def test_posix_colon_and_case(self):
        assert split_path_value("/usr/bin:/home/u/.local/bin",
                                windows=False) == \
            ["/usr/bin", "/home/u/.local/bin"]
        assert not path_contains("/Home/U/bin", "/home/u/bin", windows=False)


# ---------------------------------------------------------------------------
# find_shims
# ---------------------------------------------------------------------------


class TestFindShims:
    def test_finds_exe_on_windows_and_plain_on_posix(self, tmp_path):
        _make_shim(tmp_path, "dz")
        assert find_shims(str(tmp_path), ["dz", "dazzlecmd"]) == ["dz"]

    def test_empty_when_nothing_present(self, tmp_path):
        assert find_shims(str(tmp_path), ["dz"]) == []


# ---------------------------------------------------------------------------
# fix_windows (through the seams; runs on every platform)
# ---------------------------------------------------------------------------


SCRIPTS = r"C:\Users\u\AppData\Roaming\Python\Python313\Scripts"


@pytest.fixture
def fake_registry(monkeypatch, tmp_path):
    """Simulate HKCU Environment Path + capture writes and broadcasts."""
    state = {
        "raw": r"C:\one;%USERPROFILE%\.dotnet\tools",
        "kind": REG_EXPAND_SZ,
        "writes": [],
        "broadcasts": 0,
    }
    monkeypatch.setattr(self_setup, "_read_user_path_raw",
                        lambda: (state["raw"], state["kind"]))

    def fake_write(raw, kind):
        state["writes"].append((raw, kind))
        state["raw"], state["kind"] = raw, kind

    def fake_broadcast():
        state["broadcasts"] += 1
        return True

    monkeypatch.setattr(self_setup, "_write_user_path_raw", fake_write)
    monkeypatch.setattr(self_setup, "_broadcast_environment_change",
                        fake_broadcast)
    monkeypatch.setattr(self_setup, "_backup_dir",
                        lambda: str(tmp_path / ".dazzlecmd"))
    # The Windows-only guard: pretend we are on Windows for the flow.
    monkeypatch.setattr(self_setup.os, "name", "nt")
    return state


class TestFixWindows:
    def test_appends_and_preserves_kind(self, fake_registry):
        result = fix_windows(SCRIPTS)
        assert result.changed
        assert fake_registry["writes"] == [
            (r"C:\one;%USERPROFILE%\.dotnet\tools;" + SCRIPTS,
             REG_EXPAND_SZ),
        ]
        assert fake_registry["broadcasts"] == 1

    def test_preserves_reg_sz_kind(self, fake_registry):
        fake_registry["kind"] = REG_SZ
        result = fix_windows(SCRIPTS)
        assert result.changed
        assert fake_registry["writes"][0][1] == REG_SZ

    def test_idempotent_when_already_present(self, fake_registry):
        fake_registry["raw"] += ";" + SCRIPTS
        result = fix_windows(SCRIPTS)
        assert not result.changed
        assert fake_registry["writes"] == []
        assert "already contains" in result.message

    def test_membership_sees_expandable_spelling(self, fake_registry,
                                                 monkeypatch):
        # An entry spelled with %APPDATA% must count as present.
        monkeypatch.setenv(
            "APPDATA", r"C:\Users\u\AppData\Roaming")
        fake_registry["raw"] = (
            r"C:\one;%APPDATA%\Python\Python313\Scripts")
        result = fix_windows(SCRIPTS)
        assert not result.changed
        assert fake_registry["writes"] == []

    def test_dry_run_writes_nothing(self, fake_registry):
        result = fix_windows(SCRIPTS, dry_run=True)
        assert not result.changed
        assert fake_registry["writes"] == []
        assert fake_registry["broadcasts"] == 0
        assert result.raw_after.endswith(SCRIPTS)
        assert "dry-run" in result.message

    def test_backup_written_before_write(self, fake_registry, tmp_path):
        result = fix_windows(SCRIPTS)
        assert result.backup_file is not None
        with open(result.backup_file, encoding="utf-8") as f:
            content = f.read()
        assert f"KIND: {REG_EXPAND_SZ}" in content
        assert r"%USERPROFILE%\.dotnet\tools" in content

    def test_empty_path_value(self, fake_registry):
        fake_registry["raw"] = ""
        result = fix_windows(SCRIPTS)
        assert result.changed
        assert fake_registry["writes"][0][0] == SCRIPTS

    def test_posix_guard(self, monkeypatch):
        monkeypatch.setattr(self_setup.os, "name", "posix")
        result = fix_windows(SCRIPTS)
        assert not result.changed
        assert "advise_posix" in result.message


# ---------------------------------------------------------------------------
# advise_posix
# ---------------------------------------------------------------------------


class TestAdvisePosix:
    def test_bash(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/bin/bash")
        lines = advise_posix("/home/u/.local/bin")
        assert any("~/.bashrc" in ln for ln in lines)
        assert any('export PATH="/home/u/.local/bin:$PATH"' in ln
                   for ln in lines)

    def test_fish_uses_fish_add_path(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/usr/bin/fish")
        lines = advise_posix("/home/u/.local/bin")
        assert any("fish_add_path /home/u/.local/bin" in ln for ln in lines)

    def test_unknown_shell_generic_advice(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/opt/weird/xonsh")
        lines = advise_posix("/home/u/.local/bin")
        assert any("startup file" in ln for ln in lines)


# ---------------------------------------------------------------------------
# diagnose + report rendering (light integration, host-scheme agnostic)
# ---------------------------------------------------------------------------


class TestDiagnose:
    def test_diagnose_returns_consistent_report(self, tmp_path, monkeypatch):
        # Point the scheme machinery at a controlled scripts dir.
        monkeypatch.setattr(self_setup, "scripts_dir_for",
                            lambda scheme: str(tmp_path))
        _make_shim(tmp_path, "agg")
        monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + "/usr/bin"
                           if os.name != "nt"
                           else str(tmp_path) + os.pathsep + r"C:\Windows")
        if os.name == "nt":
            monkeypatch.setattr(self_setup, "persisted_user_path",
                                lambda: str(tmp_path))
        report = self_setup.diagnose(["agg"], package_name=None)
        assert report.shims_present == ["agg"]
        assert report.on_effective_path
        assert report.healthy
        assert not report.fixable_windows

    def test_fixable_windows_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr(self_setup, "scripts_dir_for",
                            lambda scheme: str(tmp_path))
        _make_shim(tmp_path, "agg")
        monkeypatch.setenv("PATH", "/usr/bin" if os.name != "nt"
                           else r"C:\Windows")
        monkeypatch.setattr(self_setup, "persisted_user_path",
                            lambda: r"C:\other" if os.name == "nt" else None)
        monkeypatch.setattr(self_setup.os, "name", "nt")
        report = self_setup.diagnose(["agg"])
        assert report.shims_present == ["agg"]
        assert not report.on_effective_path
        assert report.fixable_windows
        assert not report.healthy

    def test_render_report_ascii_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(self_setup, "scripts_dir_for",
                            lambda scheme: str(tmp_path))
        report = self_setup.diagnose(["agg"])
        for line in render_report(report):
            assert line == line.encode("ascii", "replace").decode("ascii")


# ---------------------------------------------------------------------------
# run_self_setup (orchestration)
# ---------------------------------------------------------------------------


class _FakeTty:
    def isatty(self):
        return True


class _FakeNoTty:
    def isatty(self):
        return False


class TestRunSelfSetup:
    def _rig_broken_windows(self, tmp_path, monkeypatch, fake_registry):
        """Shims exist; neither effective nor persisted PATH has the dir."""
        monkeypatch.setattr(self_setup, "scripts_dir_for",
                            lambda scheme: str(tmp_path))
        _make_shim(tmp_path, "agg")
        monkeypatch.setenv("PATH", r"C:\Windows")
        monkeypatch.setattr(self_setup, "persisted_user_path",
                            lambda: fake_registry["raw"])

    def test_healthy_returns_zero(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(self_setup, "scripts_dir_for",
                            lambda scheme: str(tmp_path))
        _make_shim(tmp_path, "agg")
        monkeypatch.setenv("PATH", str(tmp_path))
        if os.name == "nt":
            monkeypatch.setattr(self_setup, "persisted_user_path",
                                lambda: str(tmp_path))
        rc = self_setup.run_self_setup(["agg"])
        assert rc == 0
        assert "Everything is in order." in capsys.readouterr().out

    def test_no_shims_returns_one(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(self_setup, "scripts_dir_for",
                            lambda scheme: str(tmp_path))
        monkeypatch.setenv("PATH", r"C:\Windows" if os.name == "nt"
                           else "/usr/bin")
        if os.name == "nt":
            monkeypatch.setattr(self_setup, "persisted_user_path",
                                lambda: "")
        rc = self_setup.run_self_setup(["agg"])
        assert rc == 1
        assert "No launchers" in capsys.readouterr().out

    def test_assume_yes_applies_fix(self, tmp_path, monkeypatch,
                                    fake_registry, capsys):
        self._rig_broken_windows(tmp_path, monkeypatch, fake_registry)
        rc = self_setup.run_self_setup(["agg"], assume_yes=True)
        assert rc == 0
        assert len(fake_registry["writes"]) == 1
        assert fake_registry["writes"][0][0].endswith(str(tmp_path))
        assert fake_registry["broadcasts"] == 1

    def test_dry_run_never_writes(self, tmp_path, monkeypatch,
                                  fake_registry, capsys):
        self._rig_broken_windows(tmp_path, monkeypatch, fake_registry)
        rc = self_setup.run_self_setup(["agg"], dry_run=True)
        assert rc == 0
        assert fake_registry["writes"] == []
        assert "dry-run" in capsys.readouterr().out

    def test_tty_prompt_yes_applies(self, tmp_path, monkeypatch,
                                    fake_registry, capsys):
        self._rig_broken_windows(tmp_path, monkeypatch, fake_registry)
        monkeypatch.setattr(self_setup.sys, "stdin", _FakeTty())
        rc = self_setup.run_self_setup(["agg"], input_fn=lambda _: "y")
        assert rc == 0
        assert len(fake_registry["writes"]) == 1

    def test_tty_prompt_default_empty_applies(self, tmp_path, monkeypatch,
                                              fake_registry):
        self._rig_broken_windows(tmp_path, monkeypatch, fake_registry)
        monkeypatch.setattr(self_setup.sys, "stdin", _FakeTty())
        rc = self_setup.run_self_setup(["agg"], input_fn=lambda _: "")
        assert rc == 0
        assert len(fake_registry["writes"]) == 1

    def test_tty_prompt_no_declines(self, tmp_path, monkeypatch,
                                    fake_registry, capsys):
        self._rig_broken_windows(tmp_path, monkeypatch, fake_registry)
        monkeypatch.setattr(self_setup.sys, "stdin", _FakeTty())
        from dazzlecmd_lib import shell_fix
        monkeypatch.setattr(shell_fix, "_temp_dir", lambda: str(tmp_path))
        monkeypatch.setattr(shell_fix, "detect_invoking_shell",
                            lambda: shell_fix.SHELL_CMD)
        monkeypatch.setattr(shell_fix, "load_clipboard", lambda t: False)
        rc = self_setup.run_self_setup(["agg"], input_fn=lambda _: "n")
        assert rc == 1
        assert fake_registry["writes"] == []
        out = capsys.readouterr().out
        assert "--yes" in out
        # Decline still offers the registry-free, session-only heal.
        assert "JUST this session" in out
        assert "agg-path.cmd" in out

    def test_non_interactive_prints_exact_command(self, tmp_path,
                                                  monkeypatch,
                                                  fake_registry, capsys):
        self._rig_broken_windows(tmp_path, monkeypatch, fake_registry)
        monkeypatch.setattr(self_setup.sys, "stdin", _FakeNoTty())
        rc = self_setup.run_self_setup(["agg"], package_name="aggpkg")
        assert rc == 1
        assert fake_registry["writes"] == []
        out = capsys.readouterr().out
        assert "python -m" in out and "--yes" in out

    def test_venv_advises_activation_never_offers_registry(
            self, tmp_path, monkeypatch, fake_registry, capsys):
        # Criterion 5 (#103): a venv-local install must get "activate",
        # NEVER an offer to persist the venv's Scripts dir onto PATH.
        self._rig_broken_windows(tmp_path, monkeypatch, fake_registry)
        monkeypatch.setattr(self_setup, "detect_scheme",
                            lambda loc=None: "venv")
        rc = self_setup.run_self_setup(["agg"], assume_yes=True)
        assert rc == 1
        assert fake_registry["writes"] == []
        out = capsys.readouterr().out
        assert "Activate" in out or "activate" in out
        assert "Append this directory" not in out

    def test_posix_advises(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(self_setup, "scripts_dir_for",
                            lambda scheme: str(tmp_path))
        _make_shim(tmp_path, "agg")
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("SHELL", "/bin/bash")
        monkeypatch.setattr(self_setup, "persisted_user_path", lambda: None)
        monkeypatch.setattr(self_setup.os, "name", "posix")
        from dazzlecmd_lib import shell_fix
        monkeypatch.setattr(shell_fix, "_temp_dir", lambda: str(tmp_path))
        rc = self_setup.run_self_setup(["agg"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "~/.bashrc" in out
        # v0.10.33 symmetry: POSIX also gets the session-only heal.
        assert "agg-path.sh" in out


# ---------------------------------------------------------------------------
# first_run_hint
# ---------------------------------------------------------------------------


class TestFirstRunHint:
    def _rig(self, tmp_path, monkeypatch, on_path, shim=True,
             persisted=None):
        monkeypatch.setattr(self_setup, "scripts_dir_for",
                            lambda scheme: str(tmp_path))
        if shim:
            _make_shim(tmp_path, "agg")
        env_path = str(tmp_path) if on_path else (
            r"C:\Windows" if os.name == "nt" else "/usr/bin")
        monkeypatch.setenv("PATH", env_path)
        monkeypatch.setattr(self_setup, "persisted_user_path",
                            lambda: persisted)

    def test_silent_when_healthy(self, tmp_path, monkeypatch):
        self._rig(tmp_path, monkeypatch, on_path=True)
        assert first_run_hint(["agg"]) is None

    def test_silent_when_no_shims(self, tmp_path, monkeypatch):
        self._rig(tmp_path, monkeypatch, on_path=False, shim=False)
        assert first_run_hint(["agg"]) is None

    def test_hints_bootstrap_when_broken(self, tmp_path, monkeypatch):
        self._rig(tmp_path, monkeypatch, on_path=False)
        hint = first_run_hint(["agg"], package_name="aggpkg")
        assert hint is not None
        assert "setup" in hint
        assert "aggpkg" in hint or "agg" in hint

    @pytest.mark.skipif(os.name != "nt", reason="persisted PATH is a "
                        "Windows concept")
    def test_new_terminal_hint_when_persisted_fixed(self, tmp_path,
                                                    monkeypatch):
        self._rig(tmp_path, monkeypatch, on_path=False,
                  persisted=str(tmp_path))
        hint = first_run_hint(["agg"])
        assert hint is not None
        assert "NEW terminal" in hint


class TestLocateScriptsDir:
    """Editable/source installs: scheme inference says "system" but the
    shims live in the USER scripts dir (found live 2026-07-19 -- the
    first real `--emit-shell-fix` emitted a fix for a dir that never
    held dz.exe)."""

    def test_probes_alternate_scheme_dirs_for_shims(self, tmp_path,
                                                    monkeypatch):
        sysdir = tmp_path / "system"; sysdir.mkdir()
        userdir = tmp_path / "user"; userdir.mkdir()
        _make_shim(userdir, "agg")

        def fake_scripts_dir_for(scheme):
            return str(userdir if scheme == "user" else sysdir)

        monkeypatch.setattr(self_setup, "scripts_dir_for",
                            fake_scripts_dir_for)
        d, shims = self_setup.locate_scripts_dir("system", ["agg"])
        assert d == str(userdir)
        assert shims == ["agg"]

    def test_primary_wins_when_it_has_shims(self, tmp_path, monkeypatch):
        primary = tmp_path / "p"; primary.mkdir()
        other = tmp_path / "o"; other.mkdir()
        _make_shim(primary, "agg"); _make_shim(other, "agg")
        monkeypatch.setattr(self_setup, "scripts_dir_for",
                            lambda s: str(primary if s == "user" else primary)
                            if s == "user" else str(primary))
        d, shims = self_setup.locate_scripts_dir("user", ["agg"])
        assert d == str(primary) and shims == ["agg"]

    def test_no_shims_anywhere_returns_primary_empty(self, tmp_path,
                                                     monkeypatch):
        monkeypatch.setattr(self_setup, "scripts_dir_for",
                            lambda s: str(tmp_path))
        d, shims = self_setup.locate_scripts_dir("system", ["agg"])
        assert d == str(tmp_path) and shims == []
