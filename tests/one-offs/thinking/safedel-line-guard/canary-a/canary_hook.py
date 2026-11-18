"""Canary A: a PreToolUse hook that refuses one harmless marker command.

Purpose: find out whether a PreToolUse deny HOLDS in every permission mode
(default, acceptEdits, plan, bypassPermissions, and under an armed /goal).
Nothing is deleted anywhere; the refused command is an `echo`.

Usage (as a hook):  python -I canary_hook.py <log-file>   with the hook JSON on stdin.

Behaviour:
  command contains DZ_HOOK_CANARY -> stderr "CANARY BLOCKED mode=<permission_mode>", exit 2 (deny)
  command contains DZ_ASK_CANARY  -> stdout permissionDecision "ask", exit 0
  anything else                   -> exit 0 (allow)
Every call appends one JSON line to <log-file>: the payload's top-level keys,
tool_name, the tool_input keys, permission_mode, and what was decided -- so the
payload shape (does the PowerShell tool send tool_input.command? what are the
mode names?) is recorded as a side effect.

Stdlib only; never imports anything from the project.
"""
import json
import sys
import time


def main(argv, stdin, out, err):
    log_path = argv[1] if len(argv) > 1 else None
    raw = stdin.read()
    rec = {"ts": round(time.time(), 3)}
    decision, code = "allow", 0
    try:
        data = json.loads(raw)
        tool_input = data.get("tool_input") or {}
        cmd = tool_input.get("command")
        mode = data.get("permission_mode")
        rec.update({"keys": sorted(data.keys()), "tool_name": data.get("tool_name"),
                    "tool_input_keys": sorted(tool_input.keys()), "permission_mode": mode,
                    "hook_event_name": data.get("hook_event_name"),
                    "cmd": cmd[:200] if isinstance(cmd, str) else None})
        if isinstance(cmd, str) and "DZ_HOOK_CANARY" in cmd:
            err.write("CANARY BLOCKED mode=%s\n" % mode)
            decision, code = "deny", 2
        elif isinstance(cmd, str) and "DZ_ASK_CANARY" in cmd:
            out.write(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "ask",
                "permissionDecisionReason": "CANARY ASK mode=%s" % mode}}) + "\n")
            decision = "ask"
    except Exception as exc:  # unreadable input: allow, but record it
        rec["error"] = repr(exc)
        rec["raw"] = raw[:200]
    rec["decision"] = decision
    if log_path:
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
        except Exception:
            pass
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv, sys.stdin, sys.stdout, sys.stderr))
