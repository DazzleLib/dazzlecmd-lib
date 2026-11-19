"""Read a red-green matrix (REDGREEN_JSON from an audit script) and say which
tests have ever been turned red by a reverted behaviour, and which never have.

A test no reversion turned red is not thereby useless: it may be a guard (a
contract meant to stay green), or it may pin a behaviour the audit has no
reversion for. It is UNPROVEN here, and that list is what to read by eye.

Also reported: reversions caught by exactly one test (a single point of
detection) and tests that fail under many reversions (broad, end-to-end).

Usage: python redgreen_matrix_report.py <matrix.json> [--list-unproven]
Reads one file; writes nothing.
"""
import collections
import json
import re
import sys


def base(test_id):
    """Test function without its file prefix and parameters."""
    name = test_id.split("::")[-1]
    return re.sub(r"\[.*\]$", "", name)


def main(argv):
    path = argv[1]
    data = json.load(open(path, encoding="utf-8"))
    reversions, collected = data["reversions"], data["collected"]

    killed_by = collections.defaultdict(set)        # test function -> reversions
    for rev, failed in reversions.items():
        for t in failed:
            killed_by[base(t)].add(rev)

    by_file = collections.defaultdict(lambda: [set(), set()])   # file -> [all fns, proven fns]
    for tid in collected:
        parts = tid.split("::")
        f = parts[0] if len(parts) > 1 else "?"
        fn = base(tid)
        by_file[f][0].add(fn)
        if fn in killed_by:
            by_file[f][1].add(fn)

    all_fns = set().union(*(v[0] for v in by_file.values())) if by_file else set()
    proven = {fn for fn in all_fns if fn in killed_by}
    print("reversions run: %d   (caught by no test: %d)" % (
        len(reversions), sum(1 for f in reversions.values() if not f)))
    print("test functions collected: %d   proven (turned red at least once): %d   unproven: %d" % (
        len(all_fns), len(proven), len(all_fns) - len(proven)))
    print()
    print("%-62s %8s %8s" % ("file", "tests", "proven"))
    for f in sorted(by_file):
        fns, pr = by_file[f]
        print("%-62s %8d %8d" % (f[-62:], len(fns), len(pr)))

    single = sorted(r for r, f in reversions.items() if len({base(t) for t in f}) == 1)
    print("\nreversions caught by exactly ONE test function: %d of %d" % (len(single), len(reversions)))
    broad = sorted(((len(v), k) for k, v in killed_by.items()), reverse=True)[:8]
    print("tests that fail under the most reversions (broad, end-to-end):")
    for n, k in broad:
        print("   %3d  %s" % (n, k))

    if "--list-unproven" in argv:
        print("\nUNPROVEN test functions, by file:")
        for f in sorted(by_file):
            fns, pr = by_file[f]
            rest = sorted(fns - pr)
            if rest:
                print("  %s  (%d)" % (f, len(rest)))
                for fn in rest:
                    print("      " + fn)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
