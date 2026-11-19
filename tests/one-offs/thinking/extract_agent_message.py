"""Save a subagent's hand-back message from this session's transcript, verbatim.

A subagent's final report can arrive as an <agent-message from="<id>"> block
in the main transcript while its own task-output file is empty (seen
2026-11-18). Retyping the report into a file risks silent edits; this finds
the block and writes exactly what arrived, minus the harness's two-space
indent and its framing sentence.

Usage:
  python extract_agent_message.py <transcript.jsonl> <agent-id> <out.md> [header]

Takes the LAST such block for that agent. Read-only on the transcript;
refuses to overwrite <out.md>.
"""
import json
import os
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
    transcript, agent, out = argv[1], argv[2], argv[3]
    header = argv[4] if len(argv) > 4 else ""
    if os.path.exists(out):
        print("refusing to overwrite", out)
        return 2
    opening = '<agent-message from="%s">' % agent
    found = None
    with open(transcript, encoding="utf-8") as fh:
        for line in fh:
            if agent not in line:      # the raw line holds JSON-escaped quotes
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            for s in strings(rec):
                if opening in s and "</agent-message>" in s:
                    found = s
    if found is None:
        print("no message from", agent, "in", transcript)
        return 1
    body = found.split(opening, 1)[1].rsplit("</agent-message>", 1)[0]
    lines = body.split("\n")
    # drop the harness's framing sentence(s): everything up to the first
    # indented line is not the report
    start = next((i for i, ln in enumerate(lines) if ln.startswith("  ")), 0)
    report = [ln[2:] if ln.startswith("  ") else ln for ln in lines[start:]]
    text = "\n".join(report).strip() + "\n"
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        if header:
            fh.write(header.rstrip() + "\n\n---\n\n")
        fh.write(text)
    print("wrote %d characters to %s" % (len(text), out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
