"""Extract a subagent's final report from its task-output transcript.

A background agent's result reaches the main session as text; saving it to
disk "verbatim" by retyping it risks silent edits. This reads the agent's
JSONL transcript and writes its final report -- the input of its last
SubagentHandback call, or failing that its last assistant text -- to a file.

Usage: python extract_agent_report.py <transcript.jsonl> <out.md> [header text]
Read-only on the transcript; writes only <out.md> (refuses to overwrite).
"""
import json
import os
import sys


def walk(obj):
    """Yield every dict nested anywhere inside obj."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    src, out = argv[1], argv[2]
    header = argv[3] if len(argv) > 3 else ""
    if os.path.exists(out):
        print("refusing to overwrite", out)
        return 1
    handback = None
    last_text = None
    with open(src, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            for d in walk(rec):
                if d.get("type") == "tool_use" and "andback" in str(d.get("name", "")):
                    inp = d.get("input") or {}
                    texts = [v for v in inp.values() if isinstance(v, str)]
                    if texts:
                        handback = max(texts, key=len)
                elif d.get("type") == "text" and isinstance(d.get("text"), str):
                    if rec.get("type") == "assistant" or (rec.get("message") or {}).get("role") == "assistant":
                        last_text = d["text"]
    body = handback or last_text
    if not body:
        print("no report found in", src)
        return 1
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        if header:
            fh.write(header.replace("\\n", "\n") + "\n\n---\n\n")
        fh.write(body.rstrip() + "\n")
    print("wrote %s (%d chars, source: %s)" % (out, len(body), "handback call" if handback else "last assistant text"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
