"""POC child for the safedel line-guard design. Harmless: it only reads
process information and, in the kill modes, terminates an ancestor SHELL.

Modes:
  none        do nothing (control: the rest of the line must still run)
  show M1 M2  walk ancestors; report whether marker M1 (present on the line)
              and M2 (absent; control) appear in any ancestor's command line
  kill-parent terminate the immediate parent process
  kill-bashc  terminate the nearest ancestor whose command line is `bash ... -c ...`
"""
import sys

import psutil

mode = sys.argv[1]
me = psutil.Process()
ancestors = me.parents()

if mode == "none":
    sys.exit(0)

if mode == "show":
    # m2 (the control) arrives REVERSED and is un-reversed here, so the
    # control string itself never appears on the shell's command line.
    m1, m2 = sys.argv[2], sys.argv[3][::-1]
    hit1 = hit2 = None
    for p in ancestors:
        try:
            cl = " ".join(p.cmdline())
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            cl = "<unreadable>"
        print(f"  ancestor pid={p.pid} name={p.name()} cmdline[:140]={cl[:140]!r}")
        if m1 in cl and hit1 is None:
            hit1 = (p.pid, p.name())
        if m2 in cl and hit2 is None:
            hit2 = (p.pid, p.name())
    print(f"MARKER_PRESENT found in: {hit1}")
    print(f"MARKER_ABSENT  found in: {hit2}")
    sys.exit(0)

if mode == "kill-parent":
    parent = me.parent()
    print(f"terminating parent pid={parent.pid} name={parent.name()}", flush=True)
    parent.terminate()
    sys.exit(0)

if mode == "kill-bashc-all":
    # Under MSYS (Git Bash) the list-running shell and the fork that launched
    # us are separate bash.exe processes with the SAME command line. Terminate
    # the whole run of consecutive ancestors sharing that line, outermost last.
    target_cl, victims = None, []
    for p in ancestors:
        try:
            args = p.cmdline()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            break
        if not (p.name().lower().startswith("bash") and "-c" in args):
            if victims:
                break
            continue
        if target_cl is None:
            target_cl = args
        if args != target_cl:
            break
        victims.append(p)
    print(f"terminating {len(victims)} bash process(es): {[v.pid for v in victims]}", flush=True)
    for v in reversed(victims):   # the list-running shell first
        v.terminate()
    sys.exit(0)

if mode == "kill-bashc":
    for p in ancestors:
        try:
            args = p.cmdline()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
        if p.name().lower().startswith("bash") and "-c" in args:
            print(f"terminating bash -c ancestor pid={p.pid}", flush=True)
            p.terminate()
            sys.exit(0)
    print("no bash -c ancestor found", flush=True)
    sys.exit(3)
