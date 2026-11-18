"""Save a cross-session message from this session's transcript, verbatim.

A reply from another Claude session arrives as text inside the transcript.
Retyping it into a file risks silent edits; this finds it in the transcript
JSONL and writes exactly what arrived.

Usage:
  python extract_cross_session_message.py <transcript.jsonl> <from-name> <out.md> [header]

Finds the LAST <cross-session-message ... from-name="<from-name>" ...> block
in any string of the transcript and writes its body. Read-only on the
transcript; refuses to overwrite <out.md>.
"""
import json
import os
import re
import sys


def strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    src, who, out = argv[1], argv[2], argv[3]
    header = argv[4] if len(argv) > 4 else ""
    if os.path.exists(out):
        print("refusing to overwrite", out)
        return 1
    pat = re.compile(
        r'<cross-session-message\b[^>]*from-name="%s"[^>]*>\n?(.*?)\n?</cross-session-message>'
        % re.escape(who), re.S)
    found = None
    with open(src, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if who not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            for s in strings(rec):
                for m in pat.finditer(s):
                    found = m.group(1)
    if not found:
        print("no message from %r found in %s" % (who, src))
        return 1
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        if header:
            fh.write(header.replace("\\n", "\n") + "\n\n---\n\n")
        fh.write(found.rstrip() + "\n")
    print("wrote %s (%d chars)" % (out, len(found)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
