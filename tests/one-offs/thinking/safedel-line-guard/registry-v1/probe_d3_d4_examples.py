"""Show what the prototype judge says about the lines decisions D3 and D4 turn on.

D4: a deletion whose output is sent to the null device, or that is put in the
background with `&`. D3: deletions inside scratch or temp folders.

Only judges text; runs no command. Usage: python probe_d3_d4_examples.py
"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("_guard_probe", os.path.join(HERE, "_guard.py"))
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)

LINES = [
    ("D4 baseline: a recursive delete, alone", "rm -rf build"),
    ("D4: the same, output discarded", "rm -rf build > /dev/null 2>&1"),
    ("D4: the same, errors discarded only", "rm -rf build 2>/dev/null"),
    ("D4: the same, in the background", "rm -rf node_modules &"),
    ("D4: safedel with output discarded", "dz safedel --yes build > /dev/null 2>&1"),
    ("D3: a delete inside the system temp folder", "rm -rf /tmp/scratch-123/out"),
    ("D3: a delete inside $TMPDIR", 'rm -rf "$TMPDIR/job-7"'),
    ("D3: two deletes in a scratch folder, chained", "rm -rf /tmp/a && rm -rf /tmp/b"),
    ("D3: cleanup after a test, chained", "pytest -q && rm -rf /tmp/pytest-out"),
]

for title, line in LINES:
    verdict = G.judge(line, "bash")
    decision = getattr(verdict, "decision", None) or (verdict.get("decision") if isinstance(verdict, dict) else verdict)
    reason = getattr(verdict, "reason", None) or (verdict.get("reason") if isinstance(verdict, dict) else "")
    print("%s\n    $ %s\n    -> %s%s\n" % (title, line, decision, (": " + " ".join(str(reason).split())[:230]) if reason else ""))
