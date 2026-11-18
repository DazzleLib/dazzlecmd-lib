"""Measure the registry-v1 judge over real tool calls.  READ-ONLY: it opens transcript files
for reading and feeds each command STRING to the pure judge.  Nothing is executed.

    python measure_transcripts.py [N_FILES] [--samples K] [--dump DIR]

Counts verdicts by decision / rule / form over the N most recent Claude Code transcripts
(distinct Bash and PowerShell commands), reports judge errors and per-call latency, and prints
samples per rule so false positives can be read by eye.  --dump writes the denied lines to
DIR/denied.jsonl for a closer look (give a scratch folder).
"""
import collections
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _guard as G  # noqa: E402


def commands(files):
    seen = set()
    for f in files:
        try:
            fh = open(f, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"Bash"' not in line and '"PowerShell"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                cont = (o.get("message") or {}).get("content")
                for b in cont if isinstance(cont, list) else []:
                    if b.get("type") != "tool_use" or b.get("name") not in G.TOOL_SHELL:
                        continue
                    cmd = (b.get("input") or {}).get("command")
                    if not isinstance(cmd, str) or (b["name"], cmd) in seen:
                        continue
                    seen.add((b["name"], cmd))
                    yield G.TOOL_SHELL[b["name"]], cmd


def main(argv):
    n = int(argv[0]) if argv and argv[0].isdigit() else 150
    k = int(argv[argv.index("--samples") + 1]) if "--samples" in argv else 4
    dump = argv[argv.index("--dump") + 1] if "--dump" in argv else None
    files = sorted(glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")), key=os.path.getmtime)[-n:]
    dec = collections.Counter()
    rule = collections.Counter()
    form = collections.Counter()
    shells = collections.Counter()
    samples = collections.defaultdict(list)
    errors, denied, suspects = [], [], collections.Counter()
    total, t_all, t_max = 0, 0.0, 0.0
    for shell, cmd in commands(files):
        total += 1
        shells[shell] += 1
        t0 = time.perf_counter()
        try:
            v = G.judge(cmd, shell)
        except Exception as exc:  # a crash here is a finding
            errors.append((repr(exc), cmd[:200]))
            continue
        dt = time.perf_counter() - t0
        t_all += dt
        t_max = max(t_max, dt)
        dec[v.decision] += 1
        for s in v.suspects:
            suspects[s.split()[0] if s.split() else "?"] += 1
        if v.decision == G.DENY:
            key = "%s %s" % (v.rule, v.culprit.id)
            rule[v.rule] += 1
            form[key] += 1
            denied.append({"shell": shell, "rule": v.rule, "form": v.culprit.id, "why": v.why, "cmd": cmd})
            if len(samples[key]) < k:
                samples[key].append(cmd[:220].replace("\n", "\\n"))
        elif v.decision == G.ALLOW_POST:
            form["ALLOW-POST " + v.ids[0]] += 1
    print("files %d  distinct commands %d  %s" % (len(files), total, dict(shells)))
    print("decisions:", dict(dec))
    print("deny by rule:", dict(rule))
    print("judge errors: %d" % len(errors))
    for e in errors[:10]:
        print("   ", e)
    print("latency: mean %.3f ms, max %.1f ms" % (1000 * t_all / max(total, 1), 1000 * t_max))
    print("\nby rule+form (deny) and form (allow-post):")
    for key, c in form.most_common():
        print("%6d  %s" % (c, key))
        for s in samples.get(key, []):
            print("          e.g. %s" % s)
    print("\nunregistered-but-suspect command names (log candidates):", suspects.most_common(15))
    if dump:
        os.makedirs(dump, exist_ok=True)
        with open(os.path.join(dump, "denied.jsonl"), "w", encoding="utf-8") as fh:
            for d in denied:
                fh.write(json.dumps(d) + "\n")
        print("\ndenied lines written to", os.path.join(dump, "denied.jsonl"))


if __name__ == "__main__":
    main(sys.argv[1:])
