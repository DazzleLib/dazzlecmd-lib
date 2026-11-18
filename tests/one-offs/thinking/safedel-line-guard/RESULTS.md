# POC: can safedel see and stop the line it was called from? (2026-11-18)

Design note: `private/claude/notes/safedel/2026-11-18__11-24-44__both_safedel-as-a-deep-guard-against-dangerous-command-chains.md`.
Environment: Windows 11, Git Bash (MSYS, Git for Windows), Python 3.12, psutil 7.0.0, inside Claude Code's Bash tool. Nothing destructive: payloads echo markers into `$TMPDIR/safedel-line-guard-markers/`.

## Q1 -- can a child read the full command line it was launched from?

| Arm | Prediction | Observation | Verdict |
|---|---|---|---|
| marker placed AFTER the child on the same line | found in an ancestor's command line | found in ancestor `bash.exe -c ...` | pass |
| control marker never on the line (child un-reverses it) | not found | not found | control behaved |

First run's control was **method-broken** (the control string was passed as an argument, so it was on the line); discarded and rerun with the reversed-marker control.

**Survived.** Under Claude Code, a child process (via `psutil.Process().parents()` and `cmdline()`) sees the whole `bash -c "<line>"`, including commands queued after it.

## Q2 -- can the child stop the rest of a `;` list?

Each arm in a nested `bash -c "<child>; echo AFTER >> marker"` (`run_q2.sh`):

| Arm | Prediction | Observation |
|---|---|---|
| A child does nothing (control) | AFTER written | written |
| B terminate immediate parent | AFTER absent | **written** |
| C same, inside `| tail -1` | unknown | written |
| D terminate nearest `bash -c` ancestor, in pipeline | AFTER absent | **written** |
| E same, no pipeline | AFTER absent | **written** |
| F terminate the run of consecutive ancestor `bash.exe` with the identical command line | AFTER absent | **absent** |
| G F inside `| tail -1` | AFTER absent | **absent** |
| H F as a real Claude Code tool call (not nested) | rest of line does not run | did not run; tool returned **exit code 15**; next tool call worked |

Why B-E failed: under MSYS the bash.exe that launches a program is a fork with the same command line as the shell running the list; terminating the nearest one kills only the fork (Windows PID 50844 vs the list's shell 92660 in the diagnostic run).

**Survived, with the technique in F.** Caveats: in the nested arms the caller saw exit code 0 from the killed shell (so a guard must print its reason before killing); only Git Bash on Windows tested -- Linux/macOS bash, PowerShell, cmd.exe, background jobs (`&`), and scripts (`bash script.sh`) are untested; parsing the line for dangerous continuations is not tested here; console-wide Ctrl+C was deliberately not tried (it would reach Claude Code itself).

Fallback if a platform resists F: non-zero exit plus a harness hook (`PreToolUse`) or blocking on input.

## Related artifacts kept here (copied from the 2026-11-18 session scratchpad)

- `hookproto.py` -- the consultant's prototype hook judge (segment rule, run against 16 lines). **Evidence, not a draft:** it has three known defects -- a lone `git -C /r worktree remove X` is allowed (needs an always-deny class); `bash -c "rm -rf x; echo hi"` is allowed (quote-stripping hides the payload; scan raw text for wrappers); `2>&1` is split on `&` (drop redirections first).
- `ctime_probe.py` -- the consultant's probe showing that safedel's recovery copies directories back, so their children lose creation times (mtimes kept); control: `os.rename` keeps both.
- `test_worktree_junction_teardown.py` -- `git worktree remove` vs a `private/` junction, three cases (refused when untracked; emptied with `--force`; emptied without `--force` when `private/` is gitignored), git 2.52.0.windows.1.
- `test_safedel_junction.py` -- `dz safedel --yes` on a junction and on a folder containing one (targets intact). Its first version used `Path.resolve()` in its path check and handed safedel the junction's target -- a false alarm; the kept version uses `abspath`. **It writes to the real trash store** (scratch data only).
