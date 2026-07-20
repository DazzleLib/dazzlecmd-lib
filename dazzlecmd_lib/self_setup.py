"""Self-setup -- the aggregator's own PATH bootstrap (dazzlecmd#103).

``setup`` at aggregator level means "make ME reachable": after a
user-scheme ``pip install`` on Windows, console-script shims land in the
per-version ``%APPDATA%\\Python\\Python3XY\\Scripts`` directory, which
Windows never adds to PATH -- so ``dz`` (or any consumer aggregator's
command) is not found in any shell, forever. pip warns only on the very
first install; wheels have no post-install hooks. The one door that
always opens is ``python -m <package>``, so the bootstrap lives behind
the self-referential spelling::

    python -m dazzlecmd setup dazzlecmd      # or: setup dz
    python -m myagg setup myagg              # any dazzlecmd-lib consumer

This module provides the machinery; the ``setup`` handlers (the app's
and :mod:`dazzlecmd_lib.default_meta_commands`) special-case "the target
resolves to my own root token or alias" ahead of tool resolution and
call into here.

Pieces:

- :func:`diagnose` -- where am I installed, which scheme, where are my
  shims, is that directory on the effective and persisted PATH, what
  other copies shadow me.
- :func:`fix_windows` -- append the *verified* scripts directory to the
  persisted user PATH (``HKCU\\Environment``) via ``winreg``, preserving
  the registry value kind (``REG_EXPAND_SZ`` values commonly carry
  unexpanded ``%USERPROFILE%`` entries -- flattening them is data loss),
  after writing a timestamped backup. Never ``setx`` (it truncates
  values over 1024 chars). Broadcasts ``WM_SETTINGCHANGE`` so newly
  opened shells see the change.
- :func:`advise_posix` -- print the exact rc-file line; no uninvited
  dotfile edits.
- :func:`first_run_hint` -- a cheap check for ``__main__``: one stderr
  line pointing at the bootstrap when (and only when) it would help.

The registry access goes through module-level seams
(:func:`_read_user_path_raw`, :func:`_write_user_path_raw`,
:func:`_broadcast_environment_change`) so tests can exercise the fix
flow on any platform without touching a real registry.
"""

from __future__ import annotations

import os
import site
import sys
import sysconfig
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

# Registry value kinds, mirrored so non-Windows platforms can reason
# about them without importing winreg (winreg.REG_SZ == 1,
# winreg.REG_EXPAND_SZ == 2).
REG_SZ = 1
REG_EXPAND_SZ = 2

_WM_SETTINGCHANGE = 0x001A
_HWND_BROADCAST = 0xFFFF
_SMTO_ABORTIFHUNG = 0x0002


# ---------------------------------------------------------------------------
# PATH-entry normalization and membership -- HOMED (dazzle-filekit 0.3.3)
#
# The declared-platform comparators this module originally carried moved
# to their layering home, :mod:`dazzle_filekit.pathenv` (stack survey
# C6; the reopen trigger fired 2026-07-19). The names below stay as thin
# adapters -- fix_windows and the seam-based tests call them with the
# original ``windows``-bool spelling -- and delegate lazily so this
# module remains importable standalone.
# ---------------------------------------------------------------------------


def _platform_token(windows: Optional[bool]) -> str:
    from dazzle_filekit.pathenv import PLATFORM_POSIX, PLATFORM_WINDOWS
    if windows is None:
        windows = os.name == "nt"
    return PLATFORM_WINDOWS if windows else PLATFORM_POSIX


def _norm_entry(entry: str, windows: Optional[bool] = None) -> str:
    """Normalize one PATH entry (delegates to dazzle_filekit.pathenv)."""
    from dazzle_filekit.pathenv import normalize_path_entry
    return normalize_path_entry(entry, platform=_platform_token(windows))


def split_path_value(path_value: str,
                     windows: Optional[bool] = None) -> List[str]:
    """Split a PATH value (delegates to dazzle_filekit.pathenv)."""
    from dazzle_filekit.pathenv import split_path_value as _split
    return _split(path_value, platform=_platform_token(windows))


def path_contains(path_value: str, directory: str,
                  windows: Optional[bool] = None) -> bool:
    """Normalized membership (delegates to dazzle_filekit.pathenv)."""
    from dazzle_filekit.pathenv import path_value_contains
    return path_value_contains(path_value, directory,
                               platform=_platform_token(windows))


# ---------------------------------------------------------------------------
# Install-scheme and scripts-directory detection
# ---------------------------------------------------------------------------


def detect_scheme(package_location: Optional[str] = None) -> str:
    """Classify the running install: ``venv`` | ``user`` | ``system``.

    ``package_location`` is the directory containing the aggregator
    package (e.g. ``os.path.dirname(dazzlecmd.__file__)``); when given,
    a location under the user site-packages forces ``user``.
    """
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return "venv"
    try:
        user_site = site.getusersitepackages()
    except Exception:  # pragma: no cover - site quirks (embedded pythons)
        user_site = None
    if package_location and user_site:
        try:
            norm_loc = os.path.normcase(os.path.normpath(package_location))
            norm_user = os.path.normcase(os.path.normpath(user_site))
            if norm_loc.startswith(norm_user):
                return "user"
        except (TypeError, ValueError):
            pass
    return "system"


def scripts_dir_for(scheme: str) -> str:
    """The console-scripts directory for the given install scheme."""
    if scheme == "user":
        if hasattr(sysconfig, "get_preferred_scheme"):  # 3.10+
            user_scheme = sysconfig.get_preferred_scheme("user")
        elif os.name == "nt":
            user_scheme = "nt_user"
        elif sys.platform == "darwin" and sysconfig.get_config_var(
                "PYTHONFRAMEWORK"):
            user_scheme = "osx_framework_user"
        else:
            user_scheme = "posix_user"
        return sysconfig.get_path("scripts", user_scheme)
    return sysconfig.get_path("scripts")


def locate_scripts_dir(scheme: str,
                       command_names: List[str]) -> Tuple[str, List[str]]:
    """The scripts dir that ACTUALLY holds our launchers, probe-based.

    Scheme inference lies for editable/source installs: the package
    lives under a source checkout (no site-packages ancestry), scheme
    reads "system", yet ``pip install --user -e`` wrote the shims to
    the USER scripts dir -- and a fix aimed at the system dir helps
    nobody (found live, 2026-07-19, first real `--emit-shell-fix` use).

    Returns ``(scripts_dir, shims_present)``: the scheme-implied dir is
    tried first (so seam-based tests and the common case are
    unchanged); when it holds none of our launchers, every other
    candidate scheme dir is probed and the first one with shims wins.
    No shims anywhere -> the scheme-implied dir with an empty list,
    exactly as before.
    """
    primary = scripts_dir_for(scheme)
    shims = find_shims(primary, command_names)
    if shims:
        return primary, shims
    candidates = []
    for alt in ("user", "venv", "system"):
        if alt == scheme:
            continue
        try:
            d = scripts_dir_for(alt)
        except Exception:  # pragma: no cover - exotic sysconfig schemes
            continue
        if d and d != primary and d not in candidates:
            candidates.append(d)
    for d in candidates:
        alt_shims = find_shims(d, command_names)
        if alt_shims:
            return d, alt_shims
    return primary, []


def find_shims(scripts_dir: str, command_names: List[str]) -> List[str]:
    """Which of ``command_names`` have a launcher in ``scripts_dir``.

    Delegates to :func:`dazzlecmd_lib.paths.which_in_dir` (PATHEXT-aware
    on Windows, executable-bit-aware on POSIX -- pip writes ``.exe``
    launchers on Windows and executable scripts on POSIX). Returns the
    names (not paths) that are present. Imported lazily so this module
    stays importable standalone.
    """
    from dazzlecmd_lib.paths import which_in_dir
    return [n for n in command_names if which_in_dir(n, scripts_dir)]


def _which_all(name: str) -> List[str]:
    """Every effective-PATH hit for ``name`` (shadow-copy detection)."""
    from dazzlecmd_lib.paths import which_all_on_path
    return which_all_on_path(name)


# ---------------------------------------------------------------------------
# Windows registry seams (monkeypatchable in tests)
#
# HOMING NOTE (stack survey C1/C2): unctools is the layering home for
# generic Windows registry + persisted-environment helpers, but today its
# registry module is IE-zone-specific. When unctools grows that surface,
# these seams become thin wrappers over it (fixuser.py converges on the
# same primitives). Until then they live here so #103 stays
# self-contained and the seam-based tests keep their target.
# ---------------------------------------------------------------------------


def _read_user_path_raw() -> Tuple[str, int]:
    """Raw (unexpanded) HKCU Environment Path value and its kind.

    ``winreg.QueryValueEx`` does NOT expand ``REG_EXPAND_SZ`` -- the raw
    string, ``%USERPROFILE%`` and all, comes back verbatim. A missing
    value reads as ``("", REG_EXPAND_SZ)``.
    """
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        try:
            value, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return "", REG_EXPAND_SZ
    return value, kind


def _write_user_path_raw(raw: str, kind: int) -> None:
    """Write the HKCU Environment Path value with an explicit kind."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                        winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "Path", 0, kind, raw)


def _broadcast_environment_change() -> bool:
    """Tell running apps (Explorer et al.) the environment changed.

    New shells launched from the Start menu / Explorer pick up the new
    PATH after this; shells already running keep their stale copy.
    """
    import ctypes
    result = ctypes.c_ulong()
    ok = ctypes.windll.user32.SendMessageTimeoutW(
        _HWND_BROADCAST, _WM_SETTINGCHANGE, 0, "Environment",
        _SMTO_ABORTIFHUNG, 5000, ctypes.byref(result))
    return bool(ok)


def persisted_user_path() -> Optional[str]:
    """The persisted (registry) user PATH on Windows; None elsewhere."""
    if os.name != "nt":
        return None
    raw, _kind = _read_user_path_raw()
    return raw


# ---------------------------------------------------------------------------
# Diagnose
# ---------------------------------------------------------------------------


@dataclass
class SelfSetupReport:
    """Everything ``setup <self>`` needs to explain and decide."""

    command_names: List[str]
    package_name: Optional[str]
    package_version: Optional[str]
    package_location: Optional[str]
    python_exe: str
    scheme: str
    scripts_dir: str
    shims_present: List[str]
    on_effective_path: bool
    on_persisted_path: Optional[bool]  # None: not applicable (POSIX)
    other_copies: List[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        """Shims exist and the shell can (or will) find them."""
        if not self.shims_present:
            return False
        if self.on_effective_path:
            return True
        return bool(self.on_persisted_path)

    @property
    def needs_new_terminal(self) -> bool:
        """Persisted PATH is right but this shell predates the fix."""
        return bool(self.on_persisted_path) and not self.on_effective_path

    @property
    def fixable_windows(self) -> bool:
        """A registry append would repair reachability."""
        return (os.name == "nt" and bool(self.shims_present)
                and not self.on_effective_path
                and not bool(self.on_persisted_path))


def diagnose(command_names: List[str],
             package_name: Optional[str] = None,
             package_location: Optional[str] = None) -> SelfSetupReport:
    """Build a :class:`SelfSetupReport` for the running aggregator.

    ``command_names``: the aggregator's console-script names, most
    canonical first (e.g. ``["dz", "dazzlecmd"]``).
    ``package_name``: the pip distribution name, for version lookup.
    ``package_location``: directory of the imported package; drives
    user-scheme detection.
    """
    version = None
    if package_name:
        try:
            from importlib.metadata import version as _dist_version
            version = _dist_version(package_name)
        except Exception:
            version = None

    scheme = detect_scheme(package_location)
    scripts_dir, shims = locate_scripts_dir(scheme, command_names)

    effective = path_contains(os.environ.get("PATH", ""), scripts_dir)
    persisted_value = persisted_user_path()
    persisted: Optional[bool]
    if persisted_value is None:
        persisted = None
    else:
        persisted = path_contains(persisted_value, scripts_dir)

    others = []
    norm_scripts = _norm_entry(scripts_dir)
    for name in command_names:
        for hit in _which_all(name):
            if _norm_entry(os.path.dirname(hit)) != norm_scripts:
                others.append(hit)

    return SelfSetupReport(
        command_names=list(command_names),
        package_name=package_name,
        package_version=version,
        package_location=package_location,
        python_exe=sys.executable,
        scheme=scheme,
        scripts_dir=scripts_dir,
        shims_present=shims,
        on_effective_path=effective,
        on_persisted_path=persisted,
        other_copies=others,
    )


def render_report(report: SelfSetupReport) -> List[str]:
    """Human-readable diagnosis lines (ASCII only -- Windows codepages)."""

    def mark(ok: bool) -> str:
        return "[OK]" if ok else "[X]"

    lines = []
    title = report.command_names[0] if report.command_names else "?"
    ver = f" {report.package_version}" if report.package_version else ""
    lines.append(f"Self-setup diagnosis for {title}{ver}")
    lines.append(f"  python:      {report.python_exe}")
    lines.append(f"  install:     {report.scheme} scheme"
                 + (f" ({report.package_location})"
                    if report.package_location else ""))
    lines.append(f"  scripts dir: {report.scripts_dir}")
    if report.shims_present:
        lines.append(f"  {mark(True)} launchers present: "
                     + ", ".join(report.shims_present))
    else:
        lines.append(f"  {mark(False)} no launchers found for: "
                     + ", ".join(report.command_names))
    lines.append(f"  {mark(report.on_effective_path)} scripts dir on "
                 f"PATH (this shell)")
    if report.on_persisted_path is not None:
        lines.append(f"  {mark(bool(report.on_persisted_path))} scripts dir "
                     f"on persisted user PATH (registry)")
    for hit in report.other_copies:
        lines.append(f"  [!] another copy on PATH: {hit}")
    if report.needs_new_terminal:
        lines.append("  -> PATH is already fixed; open a NEW terminal "
                     "to pick it up.")
    return lines


# ---------------------------------------------------------------------------
# Fix (Windows) / advise (POSIX)
# ---------------------------------------------------------------------------


@dataclass
class FixResult:
    """Outcome of :func:`fix_windows`."""

    changed: bool
    message: str
    backup_file: Optional[str] = None
    raw_before: Optional[str] = None
    raw_after: Optional[str] = None


def _backup_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".dazzlecmd")


def _write_backup(raw: str, kind: int) -> str:
    bdir = _backup_dir()
    os.makedirs(bdir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bfile = os.path.join(bdir, f"path-backup-{stamp}.txt")
    with open(bfile, "w", encoding="utf-8") as f:
        f.write(f"KIND: {kind}\nRAW: {raw}\n")
    return bfile


def fix_windows(scripts_dir: str, dry_run: bool = False) -> FixResult:
    """Append ``scripts_dir`` to the persisted user PATH, safely.

    Reads the raw ``HKCU\\Environment`` Path value (unexpanded), checks
    membership with normalization (so an existing ``%APPDATA%``-spelled
    entry counts), backs the value up, appends, writes back with the
    ORIGINAL value kind, and broadcasts ``WM_SETTINGCHANGE``.

    Callers verify shims exist in ``scripts_dir`` before calling (the
    ``fixable_windows`` property); this function never adds a directory
    it was not handed.
    """
    if os.name != "nt":
        return FixResult(changed=False,
                         message="fix_windows is Windows-only; use "
                                 "advise_posix() on this platform.")

    raw, kind = _read_user_path_raw()
    if path_contains(raw, scripts_dir, windows=True):
        return FixResult(changed=False, raw_before=raw,
                         message="Persisted user PATH already contains "
                                 f"{scripts_dir}; nothing to do. "
                                 "Open a new terminal if this shell "
                                 "cannot find it.")

    new_raw = (raw.rstrip(";") + ";" + scripts_dir) if raw else scripts_dir
    if dry_run:
        return FixResult(changed=False, raw_before=raw, raw_after=new_raw,
                         message="[dry-run] would append "
                                 f"{scripts_dir} to the persisted user "
                                 "PATH (registry kind preserved).")

    backup = _write_backup(raw, kind)
    _write_user_path_raw(new_raw, kind)
    _broadcast_environment_change()
    return FixResult(changed=True, backup_file=backup,
                     raw_before=raw, raw_after=new_raw,
                     message=f"Appended {scripts_dir} to the persisted "
                             "user PATH. Open a NEW terminal to use it. "
                             f"(backup: {backup})")


def advise_posix(scripts_dir: str) -> List[str]:
    """The exact line to add, and where -- no dotfile is touched."""
    shell = os.path.basename(os.environ.get("SHELL", "")) or "sh"
    rc_map = {
        "bash": "~/.bashrc",
        "zsh": "~/.zshrc",
        "ksh": "~/.kshrc",
        "fish": "~/.config/fish/config.fish",
    }
    rc = rc_map.get(shell, "your shell's startup file")
    if shell == "fish":
        line = f"fish_add_path {scripts_dir}"
    else:
        line = f'export PATH="{scripts_dir}:$PATH"'
    return [
        f"Add this line to {rc}:",
        f"  {line}",
        "then open a new terminal (or source the file).",
    ]


# ---------------------------------------------------------------------------
# Orchestration -- the `setup <self>` flow shared by every consumer
# ---------------------------------------------------------------------------


def run_self_setup(command_names: List[str],
                   package_name: Optional[str] = None,
                   package_location: Optional[str] = None,
                   assume_yes: bool = False,
                   dry_run: bool = False,
                   emit_shell_fix: bool = False,
                   clip: bool = False,
                   input_fn=input,
                   print_fn=print) -> int:
    """The full ``setup <self>`` flow: diagnose, explain, offer the fix.

    Returns 0 when the install is (or ends up) reachable, 1 when it
    remains broken (advice given, prompt declined, nothing fixable).
    Interactive prompting happens only on a TTY; non-interactive
    callers get the exact command to run instead.

    ``emit_shell_fix``: machine channel -- write the per-shell fix
    scripts and print EXACTLY the invocation line for the detected
    shell on stdout (diagnostics to stderr), enabling eval/pipe use.
    """
    brand = command_names[0] if command_names else "dz"

    if emit_shell_fix:
        from dazzlecmd_lib import shell_fix
        report = diagnose(command_names, package_name=package_name,
                          package_location=package_location)
        paths = shell_fix.write_fix_scripts(report.scripts_dir, brand=brand)
        shell = shell_fix.detect_invoking_shell()
        if shell is None:
            shell = (shell_fix.SHELL_CMD if os.name == "nt"
                     else shell_fix.SHELL_BASH)
            print(f"note: shell not detected; emitting the {shell} form.",
                  file=sys.stderr)
        print_fn(shell_fix.invocation_line(shell, paths, brand))
        return 0

    report = diagnose(command_names, package_name=package_name,
                      package_location=package_location)
    for line in render_report(report):
        print_fn(line)

    if report.healthy:
        if report.needs_new_terminal:
            from dazzlecmd_lib import shell_fix
            shell_fix.emit_current_shell_fix(report.scripts_dir,
                                             brand=brand,
                                             print_fn=print_fn,
                                             clipboard=clip)
        else:
            print_fn("Everything is in order.")
        return 0

    if not report.shims_present:
        print_fn("No launchers were found in the scripts directory. "
                 "This usually means the package was not installed by "
                 "pip (editable/source checkout), or the install scheme "
                 "could not be detected. Nothing to fix here.")
        return 1

    if report.scheme == "venv":
        # NEVER offer to persist a venv's Scripts dir onto the user
        # PATH -- it would become stale garbage when the venv is
        # deleted. The right move is activation (dazzlecmd#103,
        # acceptance criterion 5).
        activate = os.path.join(report.scripts_dir, "activate")
        print_fn("This is a venv-local install -- the launchers belong "
                 "to the virtual environment. Activate it instead of "
                 "editing PATH:")
        if os.name == "nt":
            print_fn(f"  {activate}")
        else:
            print_fn(f"  source {activate}")
        return 1

    if os.name == "nt":
        if dry_run:
            result = fix_windows(report.scripts_dir, dry_run=True)
            print_fn(result.message)
            return 0
        if not assume_yes:
            if sys.stdin is not None and sys.stdin.isatty():
                answer = input_fn(
                    "Append this directory to your user PATH now? [Y/n] ")
                if answer.strip().lower() in ("n", "no"):
                    print_fn("Not changed. Re-run with --yes to apply, "
                             "or add the directory to PATH yourself.")
                    # Declining the PERSISTENT change doesn't mean the
                    # user wants a broken session -- offer the
                    # registry-free, this-shell-only heal.
                    from dazzlecmd_lib import shell_fix
                    print_fn("Alternatively, heal JUST this session "
                             "(no registry change):")
                    shell_fix.emit_current_shell_fix(report.scripts_dir,
                                                     brand=brand,
                                                     print_fn=print_fn,
                                                     clipboard=clip)
                    return 1
            else:
                target = (python_dash_m_target() or package_name
                          or command_names[0])
                print_fn("Non-interactive session; not changing PATH. "
                         f"Run: python -m {target} setup {target} --yes")
                return 1
        result = fix_windows(report.scripts_dir)
        print_fn(result.message)
        if result.changed:
            from dazzlecmd_lib import shell_fix
            shell_fix.emit_current_shell_fix(report.scripts_dir,
                                             brand=brand,
                                             print_fn=print_fn,
                                             clipboard=clip)
        return 0 if (result.changed or "already contains" in result.message) \
            else 1

    for line in advise_posix(report.scripts_dir):
        print_fn(line)
    # POSIX gets the same either/or as Windows (v0.10.33 symmetry):
    # persistent rc-line above, session-only heal below.
    from dazzlecmd_lib import shell_fix
    shell_fix.emit_current_shell_fix(report.scripts_dir, brand=brand,
                                     print_fn=print_fn, clipboard=clip)
    return 1


# ---------------------------------------------------------------------------
# First-run hint (for __main__)
# ---------------------------------------------------------------------------


def python_dash_m_target() -> Optional[str]:
    """The package behind ``python -m <pkg>`` for the current process.

    Returns the package name when the process was started with
    ``python -m``, else None.
    """
    main = sys.modules.get("__main__")
    pkg = getattr(main, "__package__", None)
    return pkg or None


def first_run_hint(command_names: List[str],
                   package_name: Optional[str] = None,
                   package_location: Optional[str] = None) -> Optional[str]:
    """One stderr-worthy line when the bootstrap would help, else None.

    Cheap by design: the effective-PATH string scan runs first and
    short-circuits the common healthy case before any disk or registry
    access.
    """
    scheme = detect_scheme(package_location)
    scripts_dir, shims = locate_scripts_dir(scheme, command_names)
    if not shims:
        return None
    if path_contains(os.environ.get("PATH", ""), scripts_dir):
        return None

    cmd = command_names[0] if command_names else "?"
    if os.name == "nt":
        persisted_value = persisted_user_path()
        if persisted_value is not None and path_contains(
                persisted_value, scripts_dir, windows=True):
            return (f"note: {cmd!r} is installed and PATH is already "
                    "fixed -- open a NEW terminal to use it.")
    target = python_dash_m_target() or package_name or cmd
    return (f"note: {cmd!r} is installed but its directory is not on "
            f"PATH -- run: python -m {target} setup {target}")
