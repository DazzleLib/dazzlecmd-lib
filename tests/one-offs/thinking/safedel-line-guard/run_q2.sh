#!/usr/bin/env bash
# Q2: can a child stop the rest of a `;` list? Each arm runs in a nested
# `bash -c` (so a kill cannot take down the caller) and appends AFTER to a
# scratch marker file only if the line continues past the child.
# Predictions (written before running):
#   A none                      -> AFTER written   (control: must continue)
#   B kill-parent               -> AFTER absent
#   C kill-parent | tail -1     -> unknown; predicted AFTER written (parent may not be the list's shell)
#   D kill-bashc  | tail -1     -> AFTER absent
#   E kill-bashc  (no pipe)     -> AFTER absent
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
M="${TMPDIR:-/tmp}/safedel-line-guard-markers"
mkdir -p "$M"
arm() {
    local name="$1" line="$2"
    local f="$M/$name.txt"
    : > "$f"
    bash -c "$line; echo AFTER >> '$f'" >/dev/null 2>&1
    local rc=$?
    if grep -q AFTER "$f"; then echo "$name: AFTER written  (bash rc=$rc)"; else echo "$name: AFTER ABSENT   (bash rc=$rc)"; fi
}
arm A-none          "python '$HERE/probe_child.py' none"
arm B-kill-parent   "python '$HERE/probe_child.py' kill-parent"
arm C-parent-pipe   "python '$HERE/probe_child.py' kill-parent | tail -1"
arm D-bashc-pipe    "python '$HERE/probe_child.py' kill-bashc | tail -1"
arm E-bashc         "python '$HERE/probe_child.py' kill-bashc"
# F/G added after B-E all failed: under MSYS the parent bash.exe is a fork
# stub with the same command line; the list-running shell is above it.
# Predictions: F -> AFTER absent; G (inside a pipeline) -> AFTER absent.
arm F-bashc-all     "python '$HERE/probe_child.py' kill-bashc-all"
arm G-bashc-all-pipe "python '$HERE/probe_child.py' kill-bashc-all | tail -1"
