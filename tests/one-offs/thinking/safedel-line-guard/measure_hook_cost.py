"""Measure the per-call cost of the guard as a PreToolUse hook process.

Question: what does `python -I _guard.py` add to every Bash/PowerShell tool call?
Prediction (written before the first run, 2026-11-18): ~75 ms per process in
total (the consultant's figure), most of it interpreter startup.

Arms (each N spawns, wall clock around subprocess.run):
  control   python -I -c pass                      -- interpreter startup alone
  allow     python -I _guard.py  <  {"command": "ls -la"}
  deny      python -I _guard.py  <  the 2026-11-17 line
  long      python -I _guard.py  <  a 40-segment non-destructive line

Safety (rule 1b): the guard only reads text and prints a verdict; nothing here
executes the judged commands. DZ_GUARD_LOG=off so no file is written anywhere.

Method check: the deny arm must exit 2 and the allow arm 0 on every spawn, or
the timing is of something other than the hook.
"""
import json
import os
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = os.path.join(HERE, "registry-v1", "_guard.py")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 200

INCIDENT = ('dz safedel "C:/code/x__wt/private" 2>&1 | tail -1; '
            'git -C C:/code/x worktree remove --force "C:/code/x__wt" 2>&1 | tail -1')
LONG = " && ".join("echo step%d | grep -c step" % i for i in range(40))


def payload(cmd, tool="Bash"):
    return json.dumps({"session_id": "measure", "hook_event_name": "PreToolUse",
                       "tool_name": tool, "tool_input": {"command": cmd},
                       "permission_mode": "default"}).encode("utf-8")


def spawn(args, stdin_bytes, env):
    t0 = time.perf_counter()
    p = subprocess.run([sys.executable, "-I"] + args, input=stdin_bytes,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    return (time.perf_counter() - t0) * 1000.0, p.returncode


def main():
    arms = [
        ("control", ["-c", "pass"], b"", 0),
        ("allow", [GUARD], payload("ls -la"), 0),
        ("deny", [GUARD], payload(INCIDENT), 2),
        ("long", [GUARD], payload(LONG), 0),
    ]
    env = dict(os.environ)
    env["DZ_GUARD_LOG"] = "off"
    env.pop("DZ_GUARD", None)
    for name, args, stdin_bytes, rc in arms:   # warm the file cache; discarded
        spawn(args, stdin_bytes, env)
    # Interleaved: every round samples all arms, so machine load drifts across
    # rounds, not between arms (the first run, arm-after-arm, put the control
    # arm above the guard arm -- a method artifact, not a measurement).
    times = {name: [] for name, _, _, _ in arms}
    wrong = {name: 0 for name, _, _, _ in arms}
    for _ in range(N):
        for name, args, stdin_bytes, rc in arms:
            ms, got = spawn(args, stdin_bytes, env)
            times[name].append(ms)
            if got != rc:
                wrong[name] += 1
    print("python %s   N=%d   guard=%s" % (sys.version.split()[0], N, GUARD))
    print("%-8s %8s %8s %8s %8s %9s" % ("arm", "min", "median", "p90", "max", "wrong_rc"))
    out = {}
    for name, _, _, _ in arms:
        t = sorted(times[name])
        r = {"median": statistics.median(t), "p90": t[int(N * 0.9) - 1],
             "max": t[-1], "min": t[0], "wrong_rc": wrong[name]}
        out[name] = r
        print("%-8s %8.1f %8.1f %8.1f %8.1f %9d" % (name, r["min"], r["median"], r["p90"], r["max"], r["wrong_rc"]))
    print("guard's own cost (allow median - control median): %.1f ms"
          % (out["allow"]["median"] - out["control"]["median"]))
    return 0 if all(v["wrong_rc"] == 0 for v in out.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
