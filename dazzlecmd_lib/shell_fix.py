"""Current-shell PATH healing: emitted scripts the shell applies to itself.

The OS forbids a child process from editing its parent shell's
environment -- but a script INTERPRETED BY the shell (a batch file at
the cmd prompt, a dot-sourced ``.ps1``, a ``source``d ``.sh``) runs
in-process and its changes persist. So, conda-activate style, this
module WRITES per-shell fix scripts to the temp dir and tells the user
the one invocation line for their shell (dazzlecmd#103 fast-follow;
design: the fixing-the-current-shell DWP; vision context: dazzlecmd#85,
the shell rung).

The per-shell invocation nuance is load-bearing:

    cmd         <TEMP>\\<brand>-path.cmd          (batch runs in-process)
    powershell  . $env:TEMP\\<brand>-path.ps1     (MUST be dot-sourced)
    bash/zsh    source .../<brand>-path.sh        (same sourcing rule)

Running the ``.ps1`` normally (or a ``.cmd`` from PowerShell) mutates
only a child scope and persists NOTHING -- which is why the printed
instruction is always the sourcing form.

Explicitly rejected forever (see the DWP): mutating a foreign shell
from below via process-memory injection or console keystroke injection.
Malware-shaped, terminal-multiplexer-fragile, trust-destroying. The
shell mutates itself, by consent, or not at all.

Detection uses a parent-process walk (Toolhelp32 on Windows, psutil-free)
with environment-fingerprint fallback; when detection fails the caller
prints ALL invocation lines, labeled, rather than guessing silently.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

SHELL_CMD = "cmd"
SHELL_POWERSHELL = "powershell"
SHELL_BASH = "bash"
SHELL_ZSH = "zsh"
SHELL_FISH = "fish"

_KNOWN_SHELL_EXES = {
    "cmd.exe": SHELL_CMD,
    "powershell.exe": SHELL_POWERSHELL,
    "pwsh.exe": SHELL_POWERSHELL,
    "bash.exe": SHELL_BASH,
    "zsh.exe": SHELL_ZSH,
    "fish.exe": SHELL_FISH,
    "bash": SHELL_BASH,
    "zsh": SHELL_ZSH,
    "fish": SHELL_FISH,
    "sh": SHELL_BASH,
}


# ---------------------------------------------------------------------------
# Invoking-shell detection
# ---------------------------------------------------------------------------


def _windows_process_table() -> Dict[int, Tuple[int, str]]:
    """pid -> (parent_pid, exe_name) via Toolhelp32, dependency-free.

    Seam: tests monkeypatch this (or :func:`detect_invoking_shell`).
    """
    import ctypes
    import ctypes.wintypes as wt

    TH32CS_SNAPPROCESS = 0x2
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wt.DWORD),
            ("cntUsage", wt.DWORD),
            ("th32ProcessID", wt.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wt.DWORD),
            ("cntThreads", wt.DWORD),
            ("th32ParentProcessID", wt.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wt.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    table: Dict[int, Tuple[int, str]] = {}
    if snap == INVALID_HANDLE_VALUE:
        return table
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        ok = k32.Process32First(snap, ctypes.byref(entry))
        while ok:
            table[int(entry.th32ProcessID)] = (
                int(entry.th32ParentProcessID),
                entry.szExeFile.decode(errors="replace").lower(),
            )
            ok = k32.Process32Next(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return table


def detect_invoking_shell(max_hops: int = 4) -> Optional[str]:
    """Best-effort name of the shell this process was launched from.

    Returns one of the SHELL_* constants or None (caller then shows all
    dialects). Windows: walk parent processes and match known shell
    executables. POSIX / fallback: ``$SHELL`` basename, plus MSYS
    fingerprints for git-bash on Windows.
    """
    if os.name == "nt":
        # git-bash exports these even though the ancestry is bash.exe.
        if os.environ.get("MSYSTEM") and os.environ.get("SHELL"):
            return SHELL_BASH
        try:
            table = _windows_process_table()
            pid = os.getpid()
            for _ in range(max_hops):
                parent = table.get(pid)
                if parent is None:
                    break
                ppid, exe = parent
                shell = _KNOWN_SHELL_EXES.get(exe)
                if shell:
                    return shell
                pid = ppid
        except Exception:
            pass
        return None
    shell_env = os.path.basename(os.environ.get("SHELL", ""))
    return _KNOWN_SHELL_EXES.get(shell_env)


# ---------------------------------------------------------------------------
# Script emission
# ---------------------------------------------------------------------------


def _msys_form(win_dir: str) -> str:
    """C:\\x\\y -> /c/x/y (git-bash MSYS spelling)."""
    if len(win_dir) >= 2 and win_dir[1] == ":":
        return "/" + win_dir[0].lower() + win_dir[2:].replace("\\", "/")
    return win_dir.replace("\\", "/")


def _script_bodies(scripts_dir: str, brand: str) -> Dict[str, str]:
    """Filename -> content for the three fix scripts (ASCII only)."""
    posix_dir = _msys_form(scripts_dir) if os.name == "nt" else scripts_dir
    tag = f"[{brand}]"
    cmd_body = (
        "@echo off\r\n"
        f"rem {brand} shell fix -- appends the launcher dir to THIS cmd "
        "session's PATH. Safe to re-run.\r\n"
        "rem Membership check covers both spellings: with and without a\r\n"
        "rem trailing backslash (registry entries often carry one).\r\n"
        f"echo ;%PATH%; | findstr /I /C:\";{scripts_dir};\" "
        f"/C:\";{scripts_dir}\\;\" >nul\r\n"
        "if errorlevel 1 (\r\n"
        f"  set \"PATH=%PATH%;{scripts_dir}\"\r\n"
        f"  echo {tag} PATH updated for this session.\r\n"
        ") else (\r\n"
        f"  echo {tag} already on PATH for this session.\r\n"
        ")\r\n"
    )
    ps1_body = (
        f"# {brand} shell fix -- DOT-SOURCE this (`. $env:TEMP\\"
        f"{brand}-path.ps1`); running it normally changes a child scope "
        "only.\n"
        f"$dir = '{scripts_dir}'\n"
        "$parts = $env:Path -split ';' | Where-Object { $_ }\n"
        "if ($parts -contains $dir) {\n"
        f"    Write-Host '{tag} already on PATH for this session.'\n"
        "} else {\n"
        "    $env:Path = $env:Path.TrimEnd(';') + ';' + $dir\n"
        f"    Write-Host '{tag} PATH updated for this session.'\n"
        "}\n"
    )
    sh_body = (
        f"# {brand} shell fix -- SOURCE this (`source .../{brand}-path.sh`);"
        " executing it runs a child that changes nothing.\n"
        f"_fix_dir='{posix_dir}'\n"
        "case \":$PATH:\" in\n"
        f"  *:\"$_fix_dir\":*) echo '{tag} already on PATH for this "
        "session.' ;;\n"
        "  *) PATH=\"$PATH:$_fix_dir\"; export PATH; "
        f"echo '{tag} PATH updated for this session.' ;;\n"
        "esac\n"
        "unset _fix_dir\n"
    )
    return {
        f"{brand}-path.cmd": cmd_body,
        f"{brand}-path.ps1": ps1_body,
        f"{brand}-path.sh": sh_body,
    }


def _temp_dir() -> str:
    import tempfile
    return tempfile.gettempdir()


def write_fix_scripts(scripts_dir: str, brand: str = "dz",
                      dest_dir: Optional[str] = None) -> Dict[str, str]:
    """Write the three fix scripts; returns shell-name -> script path."""
    dest = dest_dir or _temp_dir()
    paths: Dict[str, str] = {}
    bodies = _script_bodies(scripts_dir, brand)
    for filename, body in bodies.items():
        full = os.path.join(dest, filename)
        newline = "" if filename.endswith(".cmd") else "\n"
        with open(full, "w", encoding="ascii", newline=newline) as f:
            f.write(body)
        if filename.endswith(".cmd"):
            paths[SHELL_CMD] = full
        elif filename.endswith(".ps1"):
            paths[SHELL_POWERSHELL] = full
        else:
            for s in (SHELL_BASH, SHELL_ZSH, SHELL_FISH):
                paths[s] = full
    return paths


def invocation_line(shell: str, script_paths: Dict[str, str],
                    brand: str = "dz") -> str:
    """The one line the user runs in ``shell`` to heal that session."""
    if shell == SHELL_CMD:
        return f"\"%TEMP%\\{brand}-path.cmd\""
    if shell == SHELL_POWERSHELL:
        return f". \"$env:TEMP\\{brand}-path.ps1\""
    sh_path = script_paths.get(SHELL_BASH, "")
    if os.name == "nt":
        return f"source \"$TEMP/{brand}-path.sh\""
    return f"source \"{sh_path}\""


def all_invocation_lines(script_paths: Dict[str, str],
                         brand: str = "dz") -> List[Tuple[str, str]]:
    """(shell-label, invocation) for every dialect -- the fallback view."""
    return [
        (SHELL_CMD, invocation_line(SHELL_CMD, script_paths, brand)),
        (SHELL_POWERSHELL,
         invocation_line(SHELL_POWERSHELL, script_paths, brand)),
        ("bash/zsh", invocation_line(SHELL_BASH, script_paths, brand)),
    ]


def load_clipboard(text: str) -> bool:
    """Best-effort clipboard preload; True if it (probably) worked."""
    try:
        if os.name == "nt":
            proc = subprocess.run("clip", input=text.encode("utf-8"),
                                  shell=True, timeout=5)
            return proc.returncode == 0
        for tool in (["pbcopy"], ["xclip", "-selection", "clipboard"]):
            try:
                proc = subprocess.run(tool, input=text.encode("utf-8"),
                                      timeout=5)
                if proc.returncode == 0:
                    return True
            except FileNotFoundError:
                continue
        return False
    except Exception:
        return False


def emit_current_shell_fix(scripts_dir: str, brand: str = "dz",
                           print_fn=print,
                           clipboard: bool = False) -> None:
    """Write the scripts and print the healing instructions.

    Detected shell -> one invocation line. Unknown shell -> all
    dialects, labeled. The user's clipboard is NEVER touched unless
    ``clipboard=True`` (the ``--clip`` opt-in): clipboard content is
    user-owned state -- a fix tool has no business overwriting what
    might be a password or an hour of copied work uninvited.
    """
    script_paths = write_fix_scripts(scripts_dir, brand=brand)
    shell = detect_invoking_shell()
    if shell:
        line = invocation_line(shell, script_paths, brand)
        print_fn(f"To fix THIS shell right now ({shell}), run:")
        print_fn(f"  {line}")
        if clipboard and load_clipboard(line):
            print_fn("  (that line is now on your clipboard -- "
                     "paste and press Enter)")
    else:
        print_fn("To fix THIS shell right now, run the line for "
                 "your shell:")
        for label, line in all_invocation_lines(script_paths, brand):
            print_fn(f"  {label:<11} {line}")
