# Hook cost: what the guard adds to every shell tool call

Measured 2026-11-18 on plzwork (Windows 11 Pro 10.0.26200, Python 3.12.0, `python -I`), prototype `registry-v1/_guard.py` (2,214 lines, stdlib only). Script: `measure_hook_cost.py` (200 rounds, the four arms interleaved within each round).

## Question and prediction

What does running `python -I _guard.py` as a PreToolUse hook add to each Bash or PowerShell tool call? Predicted before the first run: about 75 ms per process in total (the consultant's figure), most of it interpreter startup.

## Result

| arm | what it runs | min | median | p90 | max | wrong exit codes |
|---|---|---|---|---|---|---|
| control | `python -I -c pass` | 44.5 | 51.5 | 71.2 | 198.9 | 0 / 200 |
| allow | guard on `ls -la` | 63.9 | 72.9 | 99.0 | 366.8 | 0 / 200 |
| deny | guard on the 2026-11-17 line | 63.4 | 73.5 | 95.5 | 307.8 | 0 / 200 |
| long | guard on a 40-segment harmless line | 63.0 | 73.5 | 100.6 | 399.2 | 0 / 200 |

All times in milliseconds, wall clock around the whole process.

- **A hook call costs about 73 ms (median); about 100 ms at the 90th percentile.** The prediction held.
- **About 51 ms of that is Python starting; about 21 ms is the guard** (loading the 2,214-line module and judging). The judgment itself is negligible: the 40-segment line costs the same as `ls -la`.
- Allow exits 0 and deny exits 2 on every one of the 600 guard spawns, so the timing is of the hook doing its job.
- The maximums (200-400 ms) are machine noise: a background disk survey was running during the measurement.

## Method note (a first run that was wrong)

The first run measured the arms one after another and put the control arm (85.5 ms median) *above* the guard arm (74.3 ms) -- impossible, and caused by the machine's load changing between arms. Interleaving the arms within each round fixed it. The first run's numbers are discarded.

## What it means

- At about 73 ms per shell call the hook is affordable for Claude sessions; a session making 500 shell calls spends about 37 seconds in the hook.
- The cost is dominated by interpreter startup, so the only meaningful reductions are precompiled bytecode beside the installed copy (saves part of the 21 ms) or not using Python for the hook at all. Neither is needed now.
- Not measured: the cost inside a real Claude Code session (hook dispatch overhead on top of the process), and PowerShell-tool payloads (same code path; the lexer differs).
