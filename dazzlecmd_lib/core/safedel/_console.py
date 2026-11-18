"""Is a person at the keyboard? Console detection for safedel's prompts.

``sys.stdin.isatty()`` is not that question on Windows: it is True for ANY
character device, and the NUL device is one. A tool run by an agent harness
with stdin attached to NUL therefore "is a terminal", prompts, reads
end-of-file and aborts -- which on 2026-11-17 was reported as exit 0 and let
the rest of a command line run as if a delete had happened.

``stdin_is_interactive()`` answers the real question:

- Windows: the stream's OS handle is a console (``GetConsoleMode`` succeeds),
  or it is the pipe a MSYS/Cygwin terminal (mintty, Git Bash) uses for its
  pseudo-terminal, recognised by the pipe's name the same way git does.
  NUL, an ordinary pipe and a file are not interactive.
- Elsewhere: ``isatty()``.

The Windows probes are parameters, so the decision table is testable on any
platform without a console.

Stdlib only.
"""

import re
import sys

# \msys-1888ae32e00d56aa-pty0-from-master, \cygwin-...-pty12-from-master
_MSYS_PTY = re.compile(r"(msys|cygwin)-[0-9a-f]{16}-pty\d+-(from|to)-master$",
                       re.IGNORECASE)

FILE_TYPE_CHAR = 0x0002
FILE_TYPE_PIPE = 0x0003


def _os_handle(stream):
    """The Windows OS handle behind a Python stream, or None."""
    try:
        import msvcrt
        return msvcrt.get_osfhandle(stream.fileno())
    except Exception:
        return None


def _is_console_handle(handle):
    """True if ``GetConsoleMode`` accepts the handle (a real console)."""
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE,
                                            ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        mode = wintypes.DWORD()
        return bool(kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    except Exception:
        return False


def _pipe_name(handle):
    """The name of the pipe behind a handle, or None if it is not a pipe."""
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.GetFileType.argtypes = [wintypes.HANDLE]
        kernel32.GetFileType.restype = wintypes.DWORD
        if kernel32.GetFileType(handle) != FILE_TYPE_PIPE:
            return None

        FileNameInfo = 2
        size = 4 + 2 * 1024

        class FILE_NAME_INFO(ctypes.Structure):
            _fields_ = [("FileNameLength", wintypes.DWORD),
                        ("FileName", ctypes.c_wchar * 1024)]

        info = FILE_NAME_INFO()
        kernel32.GetFileInformationByHandleEx.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
        if not kernel32.GetFileInformationByHandleEx(
                handle, FileNameInfo, ctypes.byref(info), size):
            return None
        return info.FileName[: info.FileNameLength // 2]
    except Exception:
        return None


def is_msys_pty_name(name):
    """True for the pipe name a MSYS/Cygwin pseudo-terminal uses."""
    return bool(name) and bool(_MSYS_PTY.search(name))


def stdin_is_interactive(stream=None, *, platform=None, os_handle=_os_handle,
                         is_console=_is_console_handle, pipe_name=_pipe_name):
    """True when a person can answer a prompt on ``stream`` (default stdin).

    Fails toward "not interactive": any doubt means no prompt is attempted,
    and the caller refuses instead of guessing.
    """
    if stream is None:
        stream = sys.stdin
    if stream is None:
        return False
    platform = sys.platform if platform is None else platform

    if platform != "win32":
        try:
            return bool(stream.isatty())
        except Exception:
            return False

    handle = os_handle(stream)
    if handle is None or handle == -1:
        return False
    if is_console(handle):
        return True
    return is_msys_pty_name(pipe_name(handle))
