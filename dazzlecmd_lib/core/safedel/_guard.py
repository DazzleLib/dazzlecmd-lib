#!/usr/bin/env python3
"""_guard.py -- the per-tool destructive-command registry and its pure judge.

PROTOTYPE (registry v1, 2026-11-18).  Standard library only.  Loadable standalone
(no package imports), so the same bytes can serve as

  * a Claude Code PreToolUse hook        python _guard.py            (JSON on stdin, exit 0 or 2)
  * the line guard inside `dz safedel`   judge(line, shell, ctx)     (imported or loaded by path)
  * a command-line explainer             python _guard.py --judge "<line>" --shell bash

What it is: a TRIPWIRE for honest mistakes -- an agent (or a tired human) chaining
deletions without looking at the result of each.  It is NOT a security boundary: a
script file, `mv x /dev/null`, `: > file`, `npm run clean` and many other routes are
invisible to it (see BLIND_SPOTS).

The one invariant that matters for tests (Critical Safety Rule 1b): NOTHING IN THIS
FILE EXECUTES A COMMAND OR TOUCHES A PATH NAMED ON THE LINE.  judge() is a pure
function from (text, shell, context) to a Verdict.  The only I/O is in main(): read
stdin, write stdout/stderr, append one line to the block log.

Layout of this file
  1. constants: classes, decisions, graded exit codes, post/preview kinds
  2. the lexer (bash / pwsh / cmd): quotes, redirections, heredocs, substitutions
  3. named predicates (the only code a table entry may reference)
  4. REGISTRY  -- the data table (tools -> forms) and WRAPPERS
  5. classification of one command, unwrapping of wrappers
  6. chain rules R0-R5 and judge()
  7. refusal-message rendering
  8. main(): hook mode and explain mode
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time

GUARD_VERSION = "1.0.0-proto1"

# ----------------------------------------------------------------------------
# 1. constants
# ----------------------------------------------------------------------------

# classes of a (shell, form) pair
SELF = "self-checking"      # refuses by itself when the precondition is not met (rmdir <dir>)
UNCOND = "unconditional"    # does it, no questions (rm -rf, rd /s, Remove-Item, shutil.rmtree)
DENY_CLS = "always-deny"    # refused even alone; has a recoverable replacement or a measured hazard
STAGING = "staging"         # moves data into / out of the safedel store (recoverable)
NOTD = "not-destructive"    # a registered tool in a harmless form (dry run, list, help)
CLASSES = (SELF, UNCOND, DENY_CLS, STAGING, NOTD)

# decisions
ALLOW = "ALLOW"             # nothing to check afterwards
ALLOW_POST = "ALLOW-POST"   # allowed; a post-condition kind is attached (run by a dz verb / PostToolUse)
ASK = "ASK"                 # allowed only if a human confirms (dz guarded); deny when unattended
DENY = "DENY"

# graded exit codes -- ONE definition, shared by dz safedel, dz guarded and the line guard
# (plan addendum 2026-11-18 12:50).  The hook protocol is separate: 0 = allow, 2 = block.
EXIT_OK = 0             # everything staged / allowed
EXIT_ERROR = 1          # unexpected error
EXIT_USAGE = 2          # usage error (argparse)
EXIT_REFUSED = 3        # refused / aborted -- nothing moved
EXIT_PARTIAL = 4        # some staged, some refused
EXIT_BLOCKED = 5        # blocked by the line guard (dangerous chain)
EXIT_POSTCONDITION = 6  # post-condition check failed
EXIT_CODES = {
    EXIT_OK: "ok", EXIT_ERROR: "error", EXIT_USAGE: "usage", EXIT_REFUSED: "refused",
    EXIT_PARTIAL: "partial", EXIT_BLOCKED: "blocked", EXIT_POSTCONDITION: "postcondition-failed",
}
HOOK_ALLOW, HOOK_BLOCK = 0, 2


def verdict_line(tool, outcome, detail=""):
    """The LAST line a tool prints, so `| tail -1` still shows the truth.

    verdict_line('safedel', 'STAGED', '2')      -> 'safedel: STAGED 2'
    verdict_line('guard', 'BLOCKED', 'R2 rm.recursive') -> 'guard: BLOCKED R2 rm.recursive'
    """
    return ("%s: %s %s" % (tool, outcome, detail)).rstrip()


# post-condition kinds: NAMES ONLY.  The judge never runs one; `dz guarded`, `dz safedel`
# and (if the canary passes) a PostToolUse hook look the name up and run the check.
POST_KINDS = {
    "path_absent": "every literal target no longer exists (relative targets resolved against the "
                   "cd prefix recorded in the verdict); nothing outside the targets changed",
    "junction_targets_intact": "every junction/symlink that was inside the removed tree still has a "
                               "target with the entry count recorded by the preview",
    "verdict_line": "the last output line is 'safedel: STAGED <n>' and the exit code is 0",
    "listing_matches_preview": "what was removed is exactly what the dry run / preview listed",
    "head_is_expected": "HEAD is the commit named in the preview and the snapshot taken before exists",
    "ref_hash_recorded": "the deleted ref's last commit hash was printed (git branch <name> <hash> restores it)",
    "stash_hash_recorded": "each dropped stash's hash was printed (git stash apply <hash> restores it "
                           "until gc)",
    "remote_ref_is_expected": "git ls-remote shows the ref at the pushed commit and the overwritten "
                              "hash was printed",
    "changes_saved": "the discarded working-tree changes exist in a snapshot made before the step",
    "inner": "the post-condition of the wrapped command's own registry entry",
}

# preview kinds: ('native', how) = the tool's own dry run; ('computed', how) = computed by dz and
# LABELLED "computed by dz, not by <tool>" because it cannot run the tool's real code path.
PREVIEW_NATIVE, PREVIEW_COMPUTED = "native", "computed"

YES_SCOPES = ("noninteractive", "dangerous", "delete-through-junction", "delete-through-symlink")

DEFAULT_CTX = {
    "agent": True,        # the caller is an AI agent (hook) -- False for a human's line guard
    "unattended": False,  # no human can answer an ASK (armed /goal, bypass mode)
}

BLIND_SPOTS = (
    "script files (bash x.sh, python x.py, pwsh -File x.ps1) -- only the name is on the line",
    "mv <x> /dev/null, cp /dev/null <x>, : > <x>, > <x>  (truncation and overwrite by redirection)",
    "build and package tools that delete (npm run clean, make clean, cargo clean, tox -r)",
    "aliases, shell functions and variables holding a command name with no deletion word on the line",
    "in-program deletion from an interpreter run from a file",
    "tools not in the table yet (they are logged as 'suspect' when a deletion word is visible)",
    "editors and the agent's own Write/Edit tools overwriting a file",
)

# deletion-looking words, used ONLY (a) to fail closed when a line cannot be read, and (b) to log
# unregistered commands worth a look.  Never used to allow anything.
SUSPECT = re.compile(r"""(?ix)
      (?<![\w.-]) (rm|rmdir|rd|del|erase|unlink|shred|ri|remove-item|truncate|rmtree|removedirs) (?![\w-])
    | \b (os\.(remove|unlink|rmdir|removedirs) | shutil\.rmtree | rmSync | unlinkSync | rmdirSync
         | File\.delete | FileUtils\.rm )
    | (?<![\w-]) --? (delete|purge) \b
    | (?<![\w/]) / (mir|purge) \b
    | worktree \s+ remove
    | \b git \b [^|;&\n]* \b (clean | reset \s+ --hard) \b
    | safedel
    | ::delete\( | \.delete\( | \.unlink\( | \.rmtree\(
""")

# for the "unregistered but looks destructive" log only: the command's own name, or an early argument
SUSPECT_NAME = re.compile(
    r"(?i)(^|[-_.])(rm|del|delete|remove|erase|purge|clean|clear|wipe|truncate|shred|trash|prune|unlink|mkfs)($|[-_.])")
SUSPECT_ARG = re.compile(
    r"(?i)^(--?(delete|purge|prune|remove|clean|wipe|hard)\b|(rm|remove|delete|clean|prune|purge|uninstall)$)")

# destructive calls inside an interpreter payload (python -c, node -e, perl -e, ruby -e, php -r, or a
# heredoc fed to one).  Per language, because a bare word like `unlink` is code in perl and prose in
# a Python comment.  os.rmdir / Path.rmdir / os.removedirs are deliberately absent: they refuse a
# non-empty directory (self-checking).
CODE_DELETE = {
    "python": re.compile(r"""(?x)
          \b shutil \s* \. \s* rmtree \b | \b rmtree \s* \(
        | \b os \s* \. \s* (remove|unlink) \s* \(
        | \. (unlink|rmtree) \s* \(
    """),
    "node": re.compile(r"""(?x)
          \b (rmSync|rmdirSync|unlinkSync) \b | \b fs (\.promises)? \. (rm|unlink|rmdir) \s* \(
    """),
    "perl": re.compile(r"\b(unlink|rmtree|remove_tree)\b"),
    "php": re.compile(r"\b(unlink|rmdir)\s*\("),
    "ruby": re.compile(r"\bFile\.(delete|unlink)\b|\bFileUtils\.(rm|rm_r|rm_rf|rm_f|remove_dir|remove_entry)\w*\b"
                       r"|\bDir\.(delete|unlink)\b"),
}
CODE_SHELLOUT = re.compile(
    r"\b(subprocess|os\s*\.\s*(system|popen)|child_process|execSync|spawnSync|system\s*\(|exec\s*\(|popen)\b|`")
# a deletion COMMAND written inside a shell-out string
CODE_SHELL_DELETE = re.compile(r"""(?ix)
      (?<![\w.-]) rm \s+ -[a-z]* [rf] | (?<![\w.-]) (rmdir|rd) \s+ /s | (?<![\w.-]) del \s+ /
    | remove-item | \b git \b [^\n]* \b clean \b | worktree [\s'",]+ remove
""")

# ----------------------------------------------------------------------------
# 2. the lexer
# ----------------------------------------------------------------------------


class Tok(object):
    """One word.  text: quotes removed.  literal: False if any part came from an expansion."""
    __slots__ = ("text", "quoted", "literal", "glob", "subs")

    def __init__(self, text=""):
        self.text = text
        self.quoted = False
        self.literal = True
        self.glob = False
        self.subs = []      # command lists found inside $( ) / ` ` / <( ) in this word


class Seg(object):
    """One simple command: its words, its redirections, its heredoc bodies."""
    __slots__ = ("toks", "redirs", "heredocs", "call")

    def __init__(self, toks=None):
        self.toks = toks or []
        self.redirs = []    # (operator, Tok)
        self.heredocs = []  # (body, delimiter_was_quoted)
        self.call = False   # pwsh: the segment started with the call operator  & <expr>


_GROUP = ("(", ")", "{", "}")


class _Lexer(object):
    """Splits a line into Seg objects and separator strings.  Dialects: bash, pwsh, cmd.

    Not a shell parser.  It knows exactly enough to answer three questions without being fooled
    by quoting: where do commands start, what joins them, and what text is data vs program.
    Anything it cannot read is recorded in self.doubt; the judge then fails closed.
    """

    def __init__(self, s, shell):
        self.s = s
        self.n = len(s)
        self.i = 0
        self.shell = shell
        self.doubt = []

    # -- helpers ------------------------------------------------------------
    def _balanced(self, i, op, cl):
        """Index just past the bracket matching s[i] == op (quote-aware, simple)."""
        s, n, d, j = self.s, self.n, 0, i
        while j < n:
            c = s[j]
            if c in "'\"":
                k = s.find(c, j + 1)
                j = n if k < 0 else k + 1
                continue
            if c == op:
                d += 1
            elif c == cl:
                d -= 1
                if d == 0:
                    return j + 1
            j += 1
        self.doubt.append("unbalanced %s" % op)
        return n

    def _read_heredocs(self, here):
        s, n = self.s, self.n
        for delim, quoted, strip, seg in here:
            body, found = [], False
            while self.i < n:
                j = s.find("\n", self.i)
                line = s[self.i:(j if j >= 0 else n)]
                self.i = (j + 1) if j >= 0 else n
                chk = (line.lstrip("\t") if strip else line).rstrip("\r")
                if chk == delim:
                    found = True
                    break
                body.append(line)
            if not found:
                self.doubt.append("unterminated heredoc <<%s" % delim)
            seg.heredocs.append(("\n".join(body), quoted))
        del here[:]

    def _dollar(self, t, indq):
        """bash: at '$'."""
        s, n, i = self.s, self.n, self.i
        nx = s[i + 1] if i + 1 < n else ""
        if nx == "(":
            t.literal = False
            if s.startswith("$((", i):
                j = s.find("))", i + 3)
                if j < 0:
                    self.doubt.append("unterminated $((")
                    self.i = n
                    return
                t.text += s[i:j + 2]
                self.i = j + 2
                return
            self.i = i + 2
            t.text += "$(...)"
            t.subs.append(self.run(")"))
            return
        if nx == "{":
            j = self._balanced(i + 1, "{", "}")
            body = s[i:j]
            if "$(" in body or "`" in body:
                self.doubt.append("substitution inside ${...}")
            t.text += body
            t.literal = False
            self.i = j
            return
        if nx == "'" and not indq:          # $'ansi-c'
            j = i + 2
            while j < n and s[j] != "'":
                j += 2 if s[j] == "\\" else 1
            if j >= n:
                self.doubt.append("unbalanced $' quote")
            t.text += s[i + 2:j]
            t.quoted = True
            self.i = min(j + 1, n)
            return
        if nx == '"' and not indq:          # $"locale"
            self.i = i + 1
            return
        t.text += "$"
        if nx and (nx.isalnum() or nx in "_@*#?!-$"):
            t.literal = False
        self.i = i + 1

    def _backtick(self, t):
        """bash: `command`."""
        s, n = self.s, self.n
        j = self.i + 1
        while j < n and s[j] != "`":
            j += 2 if s[j] == "\\" else 1
        if j >= n:
            self.doubt.append("unbalanced backtick")
            j = n
        sub = _Lexer(s[self.i + 1:j].replace("\\`", "`"), self.shell)
        t.subs.append(sub.run())
        self.doubt.extend(sub.doubt)
        t.text += "`...`"
        t.literal = False
        self.i = min(j + 1, n)

    def _dq(self, t):
        """Double-quoted span, bash or pwsh."""
        s, n, sh = self.s, self.n, self.shell
        t.quoted = True
        self.i += 1
        while self.i < n:
            c = s[self.i]
            nx = s[self.i + 1] if self.i + 1 < n else ""
            if c == '"':
                if sh == "pwsh" and nx == '"':
                    t.text += '"'
                    self.i += 2
                    continue
                self.i += 1
                return
            if sh == "bash":
                if c == "\\" and nx and nx in '"\\$`\n':
                    if nx != "\n":
                        t.text += nx
                    self.i += 2
                    continue
                if c == "$":
                    self._dollar(t, True)
                    continue
                if c == "`":
                    self._backtick(t)
                    continue
            else:  # pwsh
                if c == "`" and nx:
                    t.text += nx
                    self.i += 2
                    continue
                if c == "$":
                    t.literal = False
                    if nx == "(":
                        self.i += 2
                        t.text += "$(...)"
                        t.subs.append(self.run(")"))
                        continue
            t.text += c
            self.i += 1
        self.doubt.append("unbalanced double quote")

    # -- the main loop ------------------------------------------------------
    def run(self, stop=None):
        """Lex until end of text, or (stop == ')') until the closing paren of a substitution."""
        s, n, sh = self.s, self.n, self.shell
        items = []
        st = {"seg": Seg(), "tok": None, "pend": None}
        here = []
        depth = 0
        closed = stop is None

        def endtok():
            tok = st["tok"]
            if tok is None:
                return
            pend = st["pend"]
            if pend is not None:
                if pend in ("<<", "<<-"):
                    here.append([tok.text, tok.quoted, pend == "<<-", st["seg"]])
                else:
                    st["seg"].redirs.append((pend, tok))
                st["pend"] = None
            else:
                st["seg"].toks.append(tok)
            st["tok"] = None

        def endseg(sep):
            endtok()
            st["pend"] = None
            items.append(st["seg"])
            items.append(sep)
            st["seg"] = Seg()

        def cur():
            if st["tok"] is None:
                st["tok"] = Tok()
            return st["tok"]

        def redirect(op, length):
            tok = st["tok"]
            if tok is not None and not tok.quoted and (tok.text.isdigit() or tok.text == "*"):
                st["tok"] = None            # file-descriptor prefix: 2>, 1>>, *>
            else:
                endtok()
            self.i += length
            st["pend"] = op

        while self.i < n:
            c = s[self.i]
            nx = s[self.i + 1] if self.i + 1 < n else ""

            if c in " \t\r":
                endtok()
                self.i += 1
                continue
            if c == "\n":
                endseg("\n")
                self.i += 1
                if here:
                    self._read_heredocs(here)
                continue

            # ---- quoting, escapes, expansions: per dialect
            if sh == "bash":
                if c == "\\":
                    if nx == "\n":
                        self.i += 2
                        continue
                    if nx == "\r" and s[self.i + 2:self.i + 3] == "\n":
                        self.i += 3
                        continue
                    if nx:
                        t = cur()
                        t.text += nx
                        t.quoted = True
                    self.i += 2
                    continue
                if c == "'":
                    j = s.find("'", self.i + 1)
                    t = cur()
                    t.quoted = True
                    if j < 0:
                        self.doubt.append("unbalanced single quote")
                        t.text += s[self.i + 1:]
                        self.i = n
                    else:
                        t.text += s[self.i + 1:j]
                        self.i = j + 1
                    continue
                if c == '"':
                    self._dq(cur())
                    continue
                if c == "$":
                    self._dollar(cur(), False)
                    continue
                if c == "`":
                    self._backtick(cur())
                    continue
                if c == "#" and st["tok"] is None:
                    j = s.find("\n", self.i)
                    self.i = n if j < 0 else j
                    continue
                if c in "<>" and nx == "(":          # process substitution
                    t = cur()
                    t.literal = False
                    t.text += c + "(...)"
                    self.i += 2
                    t.subs.append(self.run(")"))
                    continue
                if c == "&" and nx == ">":
                    redirect("&>", 3 if s.startswith("&>>", self.i) else 2)
                    continue
                if c == "<":
                    if s.startswith("<<<", self.i):
                        redirect("<<<", 3)
                    elif s.startswith("<<-", self.i):
                        redirect("<<-", 3)
                    elif nx == "<":
                        redirect("<<", 2)
                    elif nx in "&>":
                        redirect("<" + nx, 2)
                    else:
                        redirect("<", 1)
                    continue
                if c == ">":
                    if nx and nx in ">&|":
                        redirect(">" + nx, 2)
                    else:
                        redirect(">", 1)
                    continue
                if c == ";":
                    k = 2 if nx == ";" else 1
                    endseg(";" * k)
                    self.i += k
                    continue
                if c == "&":
                    if nx == "&":
                        endseg("&&")
                        self.i += 2
                    else:
                        endseg("&")
                        self.i += 1
                    continue
                if c == "|":
                    if nx == "|":
                        endseg("||")
                        self.i += 2
                    elif nx == "&":
                        endseg("|&")
                        self.i += 2
                    else:
                        endseg("|")
                        self.i += 1
                    continue
                if c in "*?[":
                    cur().glob = True
                elif c == "{" and nx not in ("}", "", " ", "\t", "\n"):
                    cur().glob = True                 # brace expansion {a,b}

            elif sh == "pwsh":
                if c == "`":
                    if nx == "\n":
                        self.i += 2
                        continue
                    if nx == "\r" and s[self.i + 2:self.i + 3] == "\n":
                        self.i += 3
                        continue
                    if nx:
                        t = cur()
                        t.text += nx
                        t.quoted = True
                    self.i += 2
                    continue
                if c == "@" and nx and nx in "'\"" and s[self.i + 2:self.i + 4].lstrip("\r")[:1] == "\n":
                    end = "\n" + nx + "@"
                    j = s.find(end, self.i + 2)
                    t = cur()
                    t.quoted = True
                    if j < 0:
                        self.doubt.append("unterminated here-string")
                        t.text += s[self.i + 2:]
                        self.i = n
                    else:
                        body = s[self.i + 2:j].lstrip("\r\n")
                        t.text += body
                        if nx == '"' and "$" in body:
                            t.literal = False
                            if "$(" in body:
                                self.doubt.append("subexpression inside a here-string")
                        self.i = j + len(end)
                    continue
                if c == "'":
                    j, buf = self.i + 1, []
                    while j < n:
                        if s[j] == "'":
                            if j + 1 < n and s[j + 1] == "'":
                                buf.append("'")
                                j += 2
                                continue
                            break
                        buf.append(s[j])
                        j += 1
                    t = cur()
                    t.quoted = True
                    t.text += "".join(buf)
                    if j >= n:
                        self.doubt.append("unbalanced single quote")
                    self.i = min(j + 1, n)
                    continue
                if c == '"':
                    self._dq(cur())
                    continue
                if c in "$@":
                    if nx == "(":
                        t = cur()
                        t.literal = False
                        t.text += c + "(...)"
                        self.i += 2
                        t.subs.append(self.run(")"))
                        continue
                    if nx == "{":
                        j = self._balanced(self.i + 1, "{", "}")
                        t = cur()
                        t.literal = False
                        t.text += s[self.i:j]
                        self.i = j
                        continue
                    if c == "$" or (st["tok"] is None and (nx.isalpha() or nx == "_")):
                        cur().literal = False          # variable, or @splat
                    cur().text += c
                    self.i += 1
                    continue
                if c == "#" and st["tok"] is None:
                    j = s.find("\n", self.i)
                    self.i = n if j < 0 else j
                    continue
                if c == "<" and nx == "#":
                    j = s.find("#>", self.i + 2)
                    self.i = n if j < 0 else j + 2
                    continue
                if c == ">":
                    k = 2 if nx == ">" else 1
                    if s[self.i + k:self.i + k + 1] == "&":
                        redirect(">&", k + 1)
                    else:
                        redirect(">" * k, k)
                    continue
                if c == ";":
                    endseg(";")
                    self.i += 1
                    continue
                if c == "|":
                    if nx == "|":
                        endseg("||")
                        self.i += 2
                    else:
                        endseg("|")
                        self.i += 1
                    continue
                if c == "&":
                    if nx == "&":
                        endseg("&&")
                        self.i += 2
                    elif st["tok"] is None and not st["seg"].toks:
                        st["seg"].call = True           # call operator:  & "path" args
                        self.i += 1
                    else:
                        endseg("&")
                        self.i += 1
                    continue
                if c == "(" and st["tok"] is not None:  # method call / index: keep in the word
                    j = self._balanced(self.i, "(", ")")
                    st["tok"].text += s[self.i:j]
                    self.i = j
                    continue
                if c in "{}":
                    endseg(c)
                    self.i += 1
                    continue
                if c in "*?":
                    cur().glob = True

            else:  # cmd
                if c == "^":
                    if nx == "\n":
                        self.i += 2
                        continue
                    if nx:
                        t = cur()
                        t.text += nx
                        t.quoted = True
                    self.i += 2
                    continue
                if c == '"':
                    j = s.find('"', self.i + 1)
                    t = cur()
                    t.quoted = True
                    if j < 0:
                        t.text += s[self.i + 1:]
                        self.i = n
                    else:
                        t.text += s[self.i + 1:j]
                        self.i = j + 1
                    continue
                if c in "%!":
                    cur().literal = False
                if c == ">":
                    if nx == ">":
                        redirect(">>", 2)
                    elif nx == "&":
                        redirect(">&", 2)
                    else:
                        redirect(">", 1)
                    continue
                if c == "<":
                    redirect("<", 1)
                    continue
                if c == "&":
                    if nx == "&":
                        endseg("&&")
                        self.i += 2
                    else:
                        endseg("&")
                        self.i += 1
                    continue
                if c == "|":
                    if nx == "|":
                        endseg("||")
                        self.i += 2
                    else:
                        endseg("|")
                        self.i += 1
                    continue
                if c == "@" and st["tok"] is None and not st["seg"].toks:
                    self.i += 1
                    continue
                if c in "*?":
                    cur().glob = True

            # ---- grouping parentheses: all dialects
            if c == "(":
                endseg("(")
                depth += 1
                self.i += 1
                continue
            if c == ")":
                if depth > 0:
                    depth -= 1
                    endseg(")")
                    self.i += 1
                    continue
                if stop == ")":
                    self.i += 1
                    closed = True
                    break
                endseg(")")
                self.i += 1
                continue

            cur().text += c
            self.i += 1

        endtok()
        items.append(st["seg"])
        if here:
            self.doubt.append("heredoc without a body")
            del here[:]
        if not closed:
            self.doubt.append("unterminated $( or (")
        return items


# ----------------------------------------------------------------------------
# 3. named predicates -- the only code a table entry may reference
# ----------------------------------------------------------------------------
# A `when` is plain data: (name, *args) with allof / anyof / not for composition, e.g.
#     ("allof", ("sub", "reset"), ("flag", "--hard"))
# Every predicate is a pure function of (argv, env, *args).  None of them may touch the
# filesystem, the network or a process: the judge has to stay runnable on hostile text.


class _Env(object):
    __slots__ = ("shell", "ctx", "inner")

    def __init__(self, shell, ctx, inner):
        self.shell = shell
        self.ctx = ctx
        self.inner = inner      # callable(list of words) -> set of classes; for find -exec, dz guarded


def _flags(argv):
    """POSIX flags before '--': (set of '-x' short letters, list of '--long' names)."""
    short, long_ = set(), []
    for a in argv:
        if a == "--":
            break
        if a.startswith("--"):
            long_.append(a.split("=", 1)[0])
        elif a.startswith("-") and len(a) > 1:
            short.update("-" + ch for ch in a[1:])
    return short, long_


def ps_param(tok, name, minlen=1):
    """PowerShell accepts any unambiguous prefix of a parameter name: -r, -rec, -Recurse:$true."""
    if not tok.startswith("-"):
        return False
    key, _, val = tok[1:].lower().partition(":")
    return len(key) >= minlen and name.startswith(key) and val not in ("$false", "0")


def _yes(argv):
    """Scopes granted by --yes / -y, or None when absent.  '--yes' alone means noninteractive."""
    for i, a in enumerate(argv):
        if a == "--":
            break
        if a == "--yes" or (a.startswith("-") and not a.startswith("--") and "y" in a[1:]):
            val = argv[i + 1] if i + 1 < len(argv) else ""
        elif a.startswith("--yes="):
            val = a[6:]
        else:
            continue
        scopes = set(x for x in val.split(",") if x)
        if scopes and scopes <= set(YES_SCOPES):
            return scopes | {"noninteractive"}
        return {"noninteractive"}
    return None


def p_always(argv, env):
    return True


def p_flag(argv, env, *want):
    """Case-sensitive POSIX flags; short flags match inside clusters (-rf), long flags match
    exactly, with =value, or as a GNU-style unambiguous prefix of 4+ characters (--forc)."""
    short, long_ = _flags(argv)
    for w in want:
        if w.startswith("--"):
            if any(l == w or (len(l) >= 4 and w.startswith(l)) for l in long_):
                return True
        elif w in short:
            return True
    return False


def p_sub(argv, env, *words):
    """Leading subcommand words; a tuple means 'any of'."""
    if len(argv) < len(words):
        return False
    for a, w in zip(argv, words):
        if a not in (w if isinstance(w, tuple) else (w,)):
            return False
    return True


def p_arg(argv, env, *vals):
    return any(a in vals for a in argv)


def p_argci(argv, env, *vals):
    return any(a.lower() in vals for a in argv)


def p_argre(argv, env, pattern):
    return any(re.search(pattern, a) for a in argv)


def p_ps(argv, env, name, minlen=1):
    return any(ps_param(a, name, minlen) for a in argv)


def p_cmdflag(argv, env, *want):
    """cmd.exe switches, case-insensitive: /s, /S, /s/q, and MSYS-escaped //s."""
    have = set()
    for a in argv:
        if a.startswith("/"):
            for part in a.lower().lstrip("/").split("/"):
                have.add("/" + part.split(":", 1)[0])
    return any(w in have for w in want)


def p_helponly(argv, env):
    return bool(argv) and all(a in ("--help", "-h", "--version", "-?", "/?") for a in argv)


def p_ctx(argv, env, key):
    return bool(env.ctx.get(key))


def p_yes(argv, env):
    return _yes(argv) is not None


def p_yes_scope(argv, env, *scopes):
    got = _yes(argv)
    return bool(got) and any(s in got for s in scopes)


def p_find_exec(argv, env):
    """find ... -exec/-execdir/-ok/-okdir <command> ;|+  where <command> is itself a deletion."""
    for i, a in enumerate(argv):
        if a in ("-exec", "-execdir", "-ok", "-okdir"):
            inner = []
            for b in argv[i + 1:]:
                if b in (";", "+"):
                    break
                inner.append(b)
            if inner and env.inner(inner) & {UNCOND, DENY_CLS, STAGING}:
                return True
    return False


PREDICATES = {
    "always": p_always, "flag": p_flag, "sub": p_sub, "arg": p_arg, "argci": p_argci,
    "argre": p_argre, "ps": p_ps, "cmdflag": p_cmdflag, "helponly": p_helponly, "ctx": p_ctx,
    "yes": p_yes, "yes_scope": p_yes_scope, "find_exec": p_find_exec,
}
COMBINATORS = ("allof", "anyof", "not")


def _eval(expr, argv, env):
    name = expr[0]
    if name == "allof":
        return all(_eval(e, argv, env) for e in expr[1:])
    if name == "anyof":
        return any(_eval(e, argv, env) for e in expr[1:])
    if name == "not":
        return not _eval(expr[1], argv, env)
    return PREDICATES[name](argv, env, *expr[1:])


def pre_git_globals(argv):
    """Drop git's global options so `git -C /r -c a=b worktree remove w` reads `worktree remove w`."""
    notes, a = {}, list(argv)
    two = ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix",
           "--config-env", "--attr-source")
    while a:
        x = a[0]
        if x in two:
            if x == "-C" and len(a) > 1:
                notes["git_C"] = a[1]
            a = a[2:]
        elif x.startswith("-"):
            a = a[1:]
        else:
            break
    return a, notes


PRE = {"git_globals": pre_git_globals}

# ----------------------------------------------------------------------------
# 4. the registry
# ----------------------------------------------------------------------------
# REGISTRY is a list of tools.  A tool:
#     tool      display name
#     names     {shell: (command names, lower-case)}        -- recognition is per shell
#     name_re   {shell: regex on the first word}            -- optional (e.g. [IO.Directory]::Delete( )
#     pre       name in PRE                                 -- optional argv normaliser
#     forms     ordered list; the FIRST form whose `when` holds wins
# A form:
#     id        unique, dotted
#     when      predicate expression (data)
#     class     one of CLASSES                              -- per (shell, form), never per tool
#     shells    optional tuple restricting the form
#     post      name in POST_KINDS (required for unconditional / always-deny)
#     preview   (PREVIEW_NATIVE | PREVIEW_COMPUTED, how)    (required for unconditional / always-deny)
#     message   why this form is refused / what it really does -- shown to the agent
#     use       [(shell or '*', command)] the safe way; every one is fed back to the judge by a test
#     examples  [(shell, line)] each must classify to exactly this form (self-example test)
#     evidence  {level: measured|documented|argued, what, where, date}
#               -- `measured` is REQUIRED for self-checking: "looks self-checking" has already been
#                  wrong once (git worktree remove without --force, G5)
#     sole      True: must be the only command in the call (no cd-prefix exemption)
#     ask       True: needs a human's confirmation even when alone
#     override  False: `dz guarded` must not run this form
#     synthetic True: emitted by the judge itself, not matched from a command name


def E(level, what, where="", date="2026-11-18"):
    return {"level": level, "what": what, "where": where, "date": date}


def ALL(*names):
    return {"bash": names, "pwsh": names, "cmd": names}


W_BASH = "Git Bash (MSYS), git 2.52.0.windows.1, Windows 11"
W_PWSH = "pwsh 7.7 -NonInteractive, Windows 11"
W_CMD = "cmd.exe, Windows 11"
PLAN = "2026-11-18__11-40-11__DISCUSS_Rnd4_FINAL_ASSESSMENT_safedel-line-guard.md"
RND2 = "2026-11-18__12-50-02__DISCUSS_Rnd2_Brainstorm_destructive-command-registry.md"

U_SAFEDEL = ("*", "dz safedel --yes <path>")
U_SAFEDEL_DRY = ("*", "dz safedel --dry-run <path>")
M_TRASH = "It bypasses the safedel store, so nothing it removes can be recovered."

REGISTRY = [
    # ------------------------------------------------------------------ rm
    dict(tool="rm", names={"bash": ("rm",)}, forms=[
        dict(id="rm.help", when=("helponly",), **{"class": NOTD},
             examples=[("bash", "rm --help")],
             evidence=E("documented", "--help/--version print and exit", "GNU coreutils")),
        dict(id="rm.recursive", when=("flag", "-r", "-R", "--recursive"), **{"class": UNCOND},
             post="path_absent", preview=(PREVIEW_COMPUTED, "tree_listing_with_links"),
             message="rm -r removes a whole tree with no confirmation. " + M_TRASH + " What MSYS rm "
                     "does to a junction inside the tree has not been measured.",
             use=[U_SAFEDEL_DRY, U_SAFEDEL],
             examples=[("bash", "rm -rf build"), ("bash", "rm -R x"), ("bash", "/bin/rm --recursive x")],
             evidence=E("documented", "rm -r/-R/--recursive deletes recursively; -f also hides "
                        "'no such file'", "GNU coreutils rm")),
        dict(id="rm.any", when=("always",), **{"class": UNCOND},
             post="path_absent", preview=(PREVIEW_COMPUTED, "path_listing"),
             message="rm removes files with no confirmation. " + M_TRASH,
             use=[U_SAFEDEL],
             examples=[("bash", "rm notes.txt"), ("bash", "rm -f a.tmp b.tmp")],
             evidence=E("documented", "rm unlinks each operand", "GNU coreutils rm")),
    ]),
    dict(tool="unlink", names={"bash": ("unlink",)}, forms=[
        dict(id="unlink.any", when=("always",), **{"class": UNCOND},
             post="path_absent", preview=(PREVIEW_COMPUTED, "path_listing"),
             message="unlink removes one file with no confirmation. " + M_TRASH,
             use=[U_SAFEDEL], examples=[("bash", "unlink a.txt")],
             evidence=E("documented", "unlink(1) removes a single file", "GNU coreutils")),
    ]),
    dict(tool="shred", names={"bash": ("shred",)}, forms=[
        dict(id="shred.any", when=("always",), **{"class": UNCOND},
             post="path_absent", preview=(PREVIEW_COMPUTED, "path_listing"),
             message="shred overwrites file contents in place; with -u it also removes the file. " + M_TRASH,
             use=[U_SAFEDEL], examples=[("bash", "shred -u secret.key")],
             evidence=E("documented", "shred overwrites, -u removes", "GNU coreutils")),
    ]),
    # ------------------------------------------------------------------ rmdir / rd  (bash, cmd)
    dict(tool="rmdir", names={"bash": ("rmdir", "rd"), "cmd": ("rmdir", "rd")}, forms=[
        dict(id="rmdir.cmd-recursive", when=("cmdflag", "/s"), **{"class": UNCOND},
             post="path_absent", preview=(PREVIEW_COMPUTED, "tree_listing_with_links"),
             message="rd /s (rmdir /s) removes a whole tree; /q suppresses the only question it asks. " + M_TRASH,
             use=[U_SAFEDEL_DRY, U_SAFEDEL],
             examples=[("cmd", "rd /s /q build"), ("cmd", "rmdir /S build"), ("cmd", "rd /s/q build")],
             evidence=E("documented", "rd /s removes the directory tree; /q is quiet mode", W_CMD)),
        dict(id="rmdir.plain", when=("always",), **{"class": SELF}, shells=("bash", "cmd"),
             examples=[("bash", "rmdir build"), ("cmd", "rd build"), ("bash", "rmdir -p a/b")],
             evidence=E("measured", "non-empty folder: bash rmdir exit 1 'Directory not empty'; cmd rd "
                        "exit 145 'The directory is not empty.'; folder intact both times. bash rmdir "
                        "on a junction: refused, 'Not a directory', target intact (round 1).",
                        W_BASH + " / " + W_CMD)),
    ]),
    # ------------------------------------------------------------------ del / erase  (cmd)
    dict(tool="del", names={"bash": ("del", "erase"), "cmd": ("del", "erase")}, forms=[
        dict(id="del.recursive", when=("cmdflag", "/s"), **{"class": UNCOND},
             post="path_absent", preview=(PREVIEW_COMPUTED, "tree_listing_with_links"),
             message="del /s deletes matching files in every subfolder. " + M_TRASH,
             use=[U_SAFEDEL_DRY, U_SAFEDEL], examples=[("cmd", "del /s /q *.tmp")],
             evidence=E("documented", "del /s recurses; /q suppresses the wildcard prompt; /f forces "
                        "read-only files", W_CMD)),
        dict(id="del.any", when=("always",), **{"class": UNCOND},
             post="path_absent", preview=(PREVIEW_COMPUTED, "path_listing"),
             message="del (erase) deletes files with no recycle bin. " + M_TRASH,
             use=[U_SAFEDEL], examples=[("cmd", "del notes.txt"), ("cmd", "erase /q a.tmp")],
             evidence=E("documented", "del removes files permanently", W_CMD)),
    ]),
    # ------------------------------------------------------------------ Remove-Item and ALL its aliases (pwsh)
    dict(tool="Remove-Item",
         names={"pwsh": ("remove-item", "ri", "rm", "rmdir", "rd", "del", "erase")}, forms=[
             dict(id="ri.whatif", when=("anyof", ("ps", "whatif"), ("argci", "-wi")), **{"class": NOTD},
                  examples=[("pwsh", "Remove-Item build -Recurse -WhatIf"), ("pwsh", "rmdir full -r -wi")],
                  evidence=E("measured", "'rmdir full -r -wi' kept the non-empty folder", W_PWSH)),
             dict(id="ri.provider-drive", when=("argre", r"(?i)^(env|variable|function|alias):"), **{"class": NOTD},
                  examples=[("pwsh", "Remove-Item Env:CK3_REAL_SAVES -ErrorAction SilentlyContinue"),
                            ("pwsh", "ri Function:\\tmpfn")],
                  evidence=E("documented", "Env:, Variable:, Function: and Alias: are provider drives of the "
                             "session, not files; seen 2026-11-18 as a false positive in the transcript "
                             "measurement", "about_Providers")),
             dict(id="ri.recurse", when=("ps", "recurse"), **{"class": UNCOND},
                  post="path_absent", preview=(PREVIEW_NATIVE, "the same command with -WhatIf"),
                  message="In PowerShell rm, rmdir, rd, del, erase and ri are all Remove-Item, and any "
                          "prefix of -Recurse (-r, -re, -rec) deletes the whole tree. " + M_TRASH,
                  use=[U_SAFEDEL_DRY, U_SAFEDEL],
                  examples=[("pwsh", "Remove-Item build -Recurse -Force"), ("pwsh", "rmdir d -r -fo"),
                            ("pwsh", "rd d -re"), ("pwsh", "ri d -Recurse:$true")],
                  evidence=E("measured", "'rmdir full -r' and '-re' deleted a non-empty tree; "
                             "-Recurse:$false and -fo alone kept it", W_PWSH)),
             dict(id="ri.any", when=("always",), **{"class": UNCOND},
                  post="path_absent", preview=(PREVIEW_NATIVE, "the same command with -WhatIf"),
                  message="In PowerShell 'rmdir x' is Remove-Item x: it deletes x if x is a FILE, and a "
                          "non-empty folder survives only because the session cannot be prompted. It is "
                          "not the self-checking rmdir of bash or cmd. " + M_TRASH,
                  use=[U_SAFEDEL, ("pwsh", "[IO.Directory]::Delete('<dir>')"), ("pwsh", "cmd /c rd <dir>")],
                  examples=[("pwsh", "rmdir d"), ("pwsh", "Remove-Item a.txt"), ("pwsh", "del a.txt"),
                            ("pwsh", "erase a.txt"), ("pwsh", "rm a.txt"), ("pwsh", "ri a.txt"),
                            ("pwsh", "rd d -fo")],
                  evidence=E("measured", "'rmdir lonefile' deleted the file with no prompt; 'rmdir full' "
                             "kept a non-empty folder only via the NonInteractive prompt error; "
                             "Get-Command lists rm, rmdir, rd, del, erase, ri as aliases", W_PWSH)),
         ]),
    dict(tool=".NET Directory/File Delete", names={},
         name_re={"pwsh": r"(?i)^\[(system\.)?io\.(directory|file)\]::delete\("}, forms=[
             dict(id="dotnet.dir.delete.recursive",
                  when=("allof", ("argre", r"(?i)directory\]::delete\("), ("argre", r"(?i),\s*(\$true|1)\s*\)")),
                  **{"class": UNCOND}, post="path_absent", preview=(PREVIEW_COMPUTED, "tree_listing_with_links"),
                  message="[IO.Directory]::Delete(path, $true) removes the whole tree. " + M_TRASH,
                  use=[U_SAFEDEL], examples=[("pwsh", "[IO.Directory]::Delete('d', $true)")],
                  evidence=E("documented", "the recursive overload deletes subdirectories and files", ".NET")),
             dict(id="dotnet.dir.delete", when=("argre", r"(?i)directory\]::delete\("), **{"class": SELF},
                  examples=[("pwsh", "[IO.Directory]::Delete('d')"), ("pwsh", "[System.IO.Directory]::Delete('d')")],
                  evidence=E("measured", "'[IO.Directory]::Delete(full)' kept a non-empty folder: "
                             "'directory is not empty'", W_PWSH)),
             dict(id="dotnet.file.delete", when=("always",), **{"class": UNCOND},
                  post="path_absent", preview=(PREVIEW_COMPUTED, "path_listing"),
                  message="[IO.File]::Delete removes the file with no confirmation. " + M_TRASH,
                  use=[U_SAFEDEL], examples=[("pwsh", "[IO.File]::Delete('a.txt')")],
                  evidence=E("documented", "File.Delete removes the file; no error if absent", ".NET")),
         ]),
    dict(tool=".NET object Delete()", names={},
         name_re={"pwsh": r"(?i)^\$[^\s(]*\.delete\("}, forms=[
             dict(id="dotnet.obj.delete.recursive", when=("argre", r"(?i)\.delete\(\s*(\$true|1)\s*\)"),
                  **{"class": UNCOND}, post="path_absent", preview=(PREVIEW_COMPUTED, "tree_listing_with_links"),
                  message="$dir.Delete($true) removes the whole tree. " + M_TRASH,
                  use=[U_SAFEDEL], examples=[("pwsh", "$d.Delete($true)")],
                  evidence=E("documented", "DirectoryInfo.Delete(Boolean) with true deletes recursively", ".NET")),
             dict(id="dotnet.obj.delete", when=("always",), **{"class": UNCOND},
                  post="path_absent", preview=(PREVIEW_COMPUTED, "path_listing"),
                  message="$item.Delete() removes whatever $item is: a file outright, a junction's link, an "
                          "empty folder. The guard cannot see the type, so it is treated as unconditional. " + M_TRASH,
                  use=[U_SAFEDEL, ("pwsh", "[IO.Directory]::Delete('<dir>')")],
                  examples=[("pwsh", "$j.Delete()"), ("pwsh", "$item.Delete( )")],
                  evidence=E("documented", "FileInfo.Delete() deletes the file; DirectoryInfo.Delete() refuses a "
                             "non-empty directory and removes only the link of a junction", ".NET")),
         ]),
    # ------------------------------------------------------------------ find
    dict(tool="find", names={"bash": ("find",)}, forms=[
        dict(id="find.delete", when=("arg", "-delete"), **{"class": UNCOND},
             post="listing_matches_preview", preview=(PREVIEW_NATIVE, "the same find without -delete (it prints the matches)"),
             message="find -delete removes every match, depth-first, including matches you did not picture. " + M_TRASH,
             use=[("bash", "find <dir> -name <pattern> -print"), U_SAFEDEL],
             examples=[("bash", "find . -name '*.pyc' -delete")],
             evidence=E("documented", "-delete removes files, implies -depth", "GNU findutils")),
        dict(id="find.exec-delete", when=("find_exec",), **{"class": UNCOND},
             post="listing_matches_preview", preview=(PREVIEW_NATIVE, "the same find with -print instead of -exec"),
             message="find -exec <deleter> runs the deletion once per match; the targets are not on the line. " + M_TRASH,
             use=[("bash", "find <dir> -name <pattern> -print"), U_SAFEDEL],
             examples=[("bash", r"find . -name '*.tmp' -exec rm {} \;"), ("bash", "find . -type d -name x -exec rm -rf {} +")],
             evidence=E("documented", "-exec runs the command per match (or batched with +)", "GNU findutils")),
    ]),
    # ------------------------------------------------------------------ rsync / robocopy
    dict(tool="rsync", names=ALL("rsync"), forms=[
        dict(id="rsync.dry", when=("allof", ("argre", r"^--(del|delete|remove-source-files)"),
                                   ("flag", "-n", "--dry-run")), **{"class": NOTD},
             examples=[("bash", "rsync -an --delete src/ dst/"), ("bash", "rsync -a --delete --dry-run src/ dst/")],
             evidence=E("documented", "-n/--dry-run performs a trial run with no changes", "rsync(1)")),
        dict(id="rsync.delete", when=("argre", r"^--(del|delete|remove-source-files)"), **{"class": UNCOND},
             post="listing_matches_preview", preview=(PREVIEW_NATIVE, "the same command with -n (it lists 'deleting ...')"),
             message="rsync --delete removes everything at the destination that is not at the source; a "
                     "wrong or empty source empties the destination. --remove-source-files deletes the source. " + M_TRASH,
             use=[("bash", "rsync -an --delete <src>/ <dst>/")],
             examples=[("bash", "rsync -a --delete src/ dst/"), ("bash", "rsync -a --del src/ dst/"),
                       ("bash", "rsync -a --remove-source-files src/ dst/")],
             evidence=E("documented", "--delete* delete extraneous destination files", "rsync(1)")),
    ]),
    dict(tool="robocopy", names=ALL("robocopy"), forms=[
        dict(id="robocopy.list", when=("allof", ("cmdflag", "/mir", "/purge", "/move", "/mov"), ("cmdflag", "/l")),
             **{"class": NOTD}, examples=[("cmd", "robocopy src dst /MIR /L")],
             evidence=E("documented", "/L lists only: no copy, delete or timestamp", "robocopy /?")),
        dict(id="robocopy.purge", when=("cmdflag", "/mir", "/purge", "/move", "/mov"), **{"class": UNCOND},
             post="listing_matches_preview", preview=(PREVIEW_NATIVE, "the same command with /L"),
             message="robocopy /MIR and /PURGE delete everything at the destination that is not at the "
                     "source; /MOVE and /MOV delete the source after copying. " + M_TRASH,
             use=[("*", "robocopy <src> <dst> /MIR /L")],
             examples=[("cmd", "robocopy src dst /MIR"), ("pwsh", "robocopy src dst /PURGE"),
                       ("bash", "robocopy src dst //MIR"), ("cmd", "robocopy src dst /MOVE /E")],
             evidence=E("documented", "/MIR = /E /PURGE; /PURGE deletes destination extras; /MOVE moves "
                        "files and dirs", "robocopy /?")),
    ]),
    # ------------------------------------------------------------------ git
    dict(tool="git", names=ALL("git"), pre="git_globals", forms=[
        dict(id="git.worktree.remove", when=("sub", "worktree", "remove"), **{"class": DENY_CLS},
             post="junction_targets_intact", preview=(PREVIEW_COMPUTED, "worktree_links_ignored_untracked"),
             message="git worktree remove deletes THROUGH a junction or symlink inside the worktree: on "
                     "2026-11-17 it emptied two private repos this way. It does so without --force when "
                     "the link is gitignored. safedel moves the worktree folder and its links stay links.",
             use=[("*", "dz safedel --dry-run <worktree>"), ("*", "dz safedel --yes <worktree>"),
                  ("*", "git worktree prune")],
             examples=[("bash", "git worktree remove ../wt"), ("bash", "git -C /c/r worktree remove C:/w"),
                       ("bash", "git --no-pager -c a=b -C /r worktree remove --force w"),
                       ("pwsh", "git worktree remove w")],
             evidence=E("measured", "G5: empties a junction target when the junction is gitignored (no "
                        "--force needed) or with --force; refuses only when the junction shows as untracked",
                        W_BASH + "; " + PLAN)),
        dict(id="git.worktree.prune", when=("sub", "worktree", "prune"), **{"class": SELF},
             examples=[("bash", "git worktree prune"), ("bash", "git -C /r worktree prune -v")],
             evidence=E("measured", "removed only .git/worktrees/<name> for a worktree whose folder was "
                        "gone ('gitdir file points to non-existent location'); the moved folder, its "
                        "untracked file, the branch and a present worktree were all intact; -n lists "
                        "without acting. CAVEAT: a worktree on an offline drive counts as gone -- its "
                        "registration is dropped (files untouched; `git worktree repair` re-registers).",
                        W_BASH)),
        dict(id="git.clean.dry", when=("allof", ("sub", "clean"), ("flag", "-n", "--dry-run")),
             **{"class": NOTD}, examples=[("bash", "git clean -n"), ("bash", "git clean -nd"), ("bash", "git -C /r clean --dry-run -x")],
             evidence=E("measured", "'git clean -n' and 'git clean -n -f' both print 'Would remove' and "
                        "remove nothing (exit 0)", W_BASH)),
        dict(id="git.clean", when=("sub", "clean"), **{"class": DENY_CLS},
             post="listing_matches_preview", preview=(PREVIEW_NATIVE, "git clean -n with the same flags"),
             message="git clean deletes untracked (with -x: ignored) files, which by definition git cannot "
                     "bring back; -d follows into untracked folders. List first, then stage what should go.",
             use=[("*", "git clean -n"), U_SAFEDEL],
             examples=[("bash", "git clean -fd"), ("bash", "git -c core.x=1 -C /r clean -fdx"), ("pwsh", "git clean -f")],
             evidence=E("documented", "git-clean removes untracked files from the working tree", "git-clean(1); " + PLAN + " P0 Class A")),
        dict(id="git.reset.hard", when=("allof", ("sub", "reset"), ("flag", "--hard")), **{"class": UNCOND},
             post="head_is_expected", preview=(PREVIEW_COMPUTED, "git status --short plus git diff --stat HEAD and the target commit"),
             message="git reset --hard discards every uncommitted change in tracked files, staged or not, "
                     "with no way back.",
             use=[("*", 'dz git-snapshot save "before reset"'), ("*", "git status --short")],
             examples=[("bash", "git reset --hard HEAD~1"), ("bash", "git -C /r reset --hard"), ("pwsh", "git reset --hard origin/main")],
             evidence=E("documented", "--hard resets index and working tree; changes are discarded", "git-reset(1)")),
        dict(id="git.branch.force-delete",
             when=("allof", ("sub", "branch"), ("anyof", ("flag", "-D"),
                                              ("allof", ("flag", "-d", "--delete"), ("flag", "-f", "--force")))),
             **{"class": UNCOND}, post="ref_hash_recorded",
             preview=(PREVIEW_COMPUTED, "commits reachable from the branch and from no other ref"),
             message="git branch -D deletes a branch even when its commits are merged nowhere; they then "
                     "hang only on the reflog until gc. Plain -d refuses unmerged work.",
             use=[("*", "git branch -d <branch>")],
             examples=[("bash", "git branch -D topic"), ("bash", "git branch -d -f topic"), ("bash", "git branch --delete --force topic")],
             evidence=E("documented", "-D is --delete --force", "git-branch(1)")),
        dict(id="git.branch.delete", when=("allof", ("sub", "branch"), ("flag", "-d", "--delete")), **{"class": SELF},
             examples=[("bash", "git branch -d topic"), ("bash", "git -C /r branch --delete topic")],
             evidence=E("measured", "unmerged branch: 'error: the branch ... is not fully merged', exit 1, "
                        "branch kept; merged branch: deleted, hash printed", W_BASH)),
        dict(id="git.stash.drop", when=("sub", "stash", ("drop", "clear")), **{"class": UNCOND},
             post="stash_hash_recorded", preview=(PREVIEW_NATIVE, "git stash list; git stash show -p <stash>"),
             message="git stash drop removes one stash and git stash clear removes all of them; the commits "
                     "become unreachable.",
             use=[("*", "git stash list"), ("*", "git stash show -p stash@{0}")],
             examples=[("bash", "git stash drop"), ("bash", "git stash clear"), ("bash", "git stash drop stash@{1}")],
             evidence=E("documented", "drop removes a single entry, clear removes all", "git-stash(1)")),
        dict(id="git.push.force",
             when=("allof", ("sub", "push"),
                   ("anyof", ("flag", "-f", "--force", "--force-with-lease", "--force-if-includes", "--mirror",
                              "--delete", "-d", "--prune"), ("argre", r"^\+."), ("argre", r"^:[^:]"))),
             **{"class": UNCOND}, post="remote_ref_is_expected",
             preview=(PREVIEW_NATIVE, "git push --dry-run with the same arguments; git log <remote>/<branch>..HEAD and HEAD..<remote>/<branch>"),
             message="A forced push (or +refspec, --mirror, --delete, :branch) overwrites or deletes commits "
                     "on the remote, for everyone. --force-with-lease refuses when the remote moved.",
             use=[("*", "git push --force-with-lease origin <branch>"), ("*", "git push --dry-run origin <branch>")],
             examples=[("bash", "git push -f origin main"), ("bash", "git push --force"), ("bash", "git push origin +main"),
                       ("bash", "git push origin :old-branch"), ("bash", "git push origin --delete old")],
             evidence=E("documented", "--force disables the fast-forward check; --force-with-lease keeps a "
                        "compare-and-swap on the remote ref (not measured here, so not classed self-checking)",
                        "git-push(1)")),
        dict(id="git.checkout.discard",
             when=("allof", ("sub", "checkout"), ("anyof", ("arg", "--", "."), ("flag", "-f", "--force"))),
             **{"class": UNCOND}, post="changes_saved", preview=(PREVIEW_NATIVE, "git diff --stat -- <paths>"),
             message="git checkout -- <paths> (or '.', or -f) overwrites uncommitted changes in those files.",
             use=[("*", 'dz git-snapshot save "before discard"'), ("*", "git diff --stat")],
             examples=[("bash", "git checkout -- ."), ("bash", "git checkout ."), ("bash", "git checkout -f main"),
                       ("bash", "git checkout HEAD -- src/a.py")],
             evidence=E("documented", "checkout <pathspec> replaces working-tree files from the index/tree", "git-checkout(1)")),
        dict(id="git.restore.worktree",
             when=("allof", ("sub", "restore"),
                   ("anyof", ("not", ("flag", "--staged", "-S")), ("flag", "--worktree", "-W"))),
             **{"class": UNCOND}, post="changes_saved", preview=(PREVIEW_NATIVE, "git diff --stat -- <paths>"),
             message="git restore <paths> overwrites uncommitted working-tree changes in those files.",
             use=[("*", 'dz git-snapshot save "before discard"'), ("*", "git diff --stat")],
             examples=[("bash", "git restore ."), ("bash", "git restore --staged --worktree a.py")],
             evidence=E("documented", "restore defaults to --worktree; --staged alone only unstages", "git-restore(1)")),
    ]),
    # ------------------------------------------------------------------ dz safedel / dz guarded
    dict(tool="dz", names=ALL("dz"), forms=[
        dict(id="safedel.readonly",
             when=("allof", ("sub", "safedel"),
                   ("anyof", ("sub", "safedel", ("list", "status", "guard")), ("flag", "-n", "--dry-run"),
                    ("flag", "-h", "--help"))),
             **{"class": NOTD},
             examples=[("bash", "dz safedel list"), ("bash", "dz safedel status"), ("bash", "dz safedel --dry-run a"),
                       ("bash", "dz safedel recover last --dry-run"), ("bash", "dz safedel guard status")],
             evidence=E("measured", "the 2026-11-17 dry run printed 'DRY RUN: No files were modified.'", "dazzlecmd 0.12.24; incident postmortem")),
        dict(id="safedel.clean", when=("sub", "safedel", "clean"), **{"class": DENY_CLS},
             post="listing_matches_preview", preview=(PREVIEW_NATIVE, "dz safedel list"), override=False,
             message="dz safedel clean permanently deletes trash entries. It is reserved for the human at a "
                     "console; an agent never cleans the store.",
             use=[("*", "dz safedel list"), ("*", "dz safedel status")],
             examples=[("bash", "dz safedel clean --age '>30d'"), ("pwsh", "dz safedel clean --yes")],
             evidence=E("documented", "maintainer rule: 'Do NOT use dz safedel clean ... reserved for humans'", "global.md rule 1a")),
        dict(id="safedel.recover", when=("sub", "safedel", "recover"), **{"class": STAGING},
             post="verdict_line", preview=(PREVIEW_NATIVE, "dz safedel recover <which> --dry-run"),
             message="dz safedel recover moves items out of the store, back to their place.",
             use=[("*", "dz safedel recover last --dry-run"), ("*", "dz safedel recover last")],
             examples=[("bash", "dz safedel recover last")],
             evidence=E("measured", "G7: recovery copies the tree back, then removes the trash copy", PLAN)),
        dict(id="safedel.stage.noyes", when=("allof", ("ctx", "agent"), ("sub", "safedel"), ("not", ("yes",))),
             **{"class": DENY_CLS}, post="verdict_line", preview=(PREVIEW_NATIVE, "dz safedel --dry-run <path>"),
             override=False,
             message="Without --yes, dz safedel asks 'Proceed with deletion? [y/N]', gets no answer from a "
                     "tool call, prints 'Aborted.' and (dazzlecmd 0.12.24) EXITS 0 with nothing moved. That "
                     "silent refusal is step one of the 2026-11-17 loss.",
             use=[U_SAFEDEL_DRY, U_SAFEDEL],
             examples=[("bash", "dz safedel build"), ("bash", 'dz safedel "C:/w/private"')],
             evidence=E("measured", "G2: `dz safedel x </dev/null` -> 'Aborted.', exit=0, file intact. "
                        "LIFECYCLE: drop this form once the installed safedel refuses with exit 3.", "dazzlecmd 0.12.24 / dazzlecmd-lib 0.10.36; " + PLAN)),
        dict(id="safedel.stage.scoped",
             when=("allof", ("sub", "safedel"),
                   ("yes_scope", "dangerous", "delete-through-junction", "delete-through-symlink")),
             **{"class": STAGING}, sole=True, post="verdict_line", preview=(PREVIEW_NATIVE, "the same command with --dry-run"),
             message="A scoped --yes (dangerous, delete-through-junction, delete-through-symlink) asks safedel "
                     "to do what it refuses by default. It must be the only command in the call.",
             use=[("*", "dz safedel --dry-run <path>")],
             examples=[("bash", "dz safedel --yes delete-through-junction w/private"),
                       ("bash", "dz safedel --yes noninteractive,dangerous x")],
             evidence=E("argued", "scoped --yes values decided by the maintainer 2026-11-18 12:27; not built yet", PLAN)),
        dict(id="safedel.stage", when=("sub", "safedel"), **{"class": STAGING},
             post="verdict_line", preview=(PREVIEW_NATIVE, "dz safedel --dry-run <path>"),
             message="dz safedel moves the targets into the store. Join it to other commands only with &&, "
                     "never pipe it, and read its last line.",
             use=[U_SAFEDEL],
             examples=[("bash", "dz safedel --yes build"), ("bash", "dz safedel -y a b"), ("pwsh", "dz safedel --yes noninteractive a")],
             evidence=E("measured", "G3: --yes <junction> removes only the link; --yes <folder holding a "
                        "junction> moves the folder, the junction travels as a link; targets intact", PLAN)),
        dict(id="guarded.run", when=("allof", ("sub", "guarded"), ("yes_scope", "dangerous")),
             **{"class": UNCOND}, sole=True, ask=True, post="inner", preview=(PREVIEW_NATIVE, "dz guarded -- <command>"),
             message="dz guarded --yes dangerous runs a command the guard refuses, after printing its preview, "
                     "and checks the post-condition afterwards. It needs a human's confirmation and must be "
                     "the only command in the call.",
             use=[("*", "dz guarded -- <command>")],
             examples=[("bash", "dz guarded --yes dangerous -- git worktree remove w"),
                       ("pwsh", "dz guarded --yes dangerous -- git clean -fd")],
             evidence=E("argued", "verb designed in round 2; not built yet", RND2)),
        dict(id="guarded.preview", when=("sub", "guarded"), **{"class": NOTD},
             examples=[("bash", "dz guarded -- git worktree remove w"), ("bash", "dz guarded --help")],
             evidence=E("argued", "without --yes dangerous the verb only prints the preview and exits 3", RND2)),
    ]),
    # ------------------------------------------------------------------ forms emitted by the judge itself
    dict(tool="wrapper", names={}, forms=[
        dict(id="xargs.delete", when=("always",), **{"class": DENY_CLS}, synthetic=True, override=False,
             message="xargs (or parallel) feeding a deletion: the targets come from a pipe, so neither you "
                     "nor the guard can see what will be removed, and one bad line in the list is enough.",
             use=[("bash", "find <dir> -name <pattern> -print"), U_SAFEDEL],
             examples=[("bash", "xargs rm -f"), ("bash", "xargs -0 -n 1 rm"), ("bash", "parallel rm -rf {}"),
                       ("bash", "xargs -I{} dz safedel --yes {}")],
             evidence=E("argued", "the target list is data from a pipe; " + PLAN + " P0 wrapper rule")),
        dict(id="interp.delete", when=("always",), **{"class": DENY_CLS}, synthetic=True,
             post="path_absent", preview=(PREVIEW_COMPUTED, "none: the payload is a program, not a command"),
             message="An interpreter one-liner that deletes (shutil.rmtree, os.remove, os.unlink, fs.rmSync, "
                     "unlink, File.delete ...) is rm with the label taken off: no trash, no confirmation, and "
                     "a loop or glob inside it is invisible. Do the deletion as a command.",
             use=[U_SAFEDEL_DRY, U_SAFEDEL],
             examples=[("bash", "python -c \"import shutil; shutil.rmtree('x')\""),
                       ("bash", "python3 -c 'import os; os.remove(\"a.txt\")'"),
                       ("bash", "node -e \"require('fs').rmSync('x', {recursive: true})\""),
                       ("pwsh", "python -c \"import os; os.unlink('a')\""),
                       ("bash", "python -c \"import os; os.system('rm -rf x')\"")],
             evidence=E("argued", "round 3 of the guard consultation: after a deny the cheapest next move is "
                        "python -c shutil.rmtree; 12:45 addendum names shutil.rmtree as unconditional", PLAN)),
        dict(id="guard.doubt", when=("always",), **{"class": DENY_CLS}, synthetic=True, override=False,
             message="The guard could not read this line (unbalanced quote, unterminated $( ) or heredoc) "
                     "and it contains a deletion word, so it is refused rather than guessed at.",
             use=[U_SAFEDEL],
             examples=[("bash", "echo \"rm -rf x"), ("bash", "echo $(rm -rf x")],
             evidence=E("argued", "fail closed on parse doubt when a deletion verb is present; " + PLAN + " P0")),
        dict(id="guard.opaque", when=("always",), **{"class": DENY_CLS}, synthetic=True, override=False,
             message="A command name on this line comes from an expansion ($VAR, \"$@\", %VAR%) or from text "
                     "piped into a shell, and the line contains a deletion word: the guard cannot see what "
                     "will run. Write the command out.",
             use=[U_SAFEDEL],
             examples=[("bash", "$TOOL -rf build; echo rm"), ("bash", "echo 'rm -rf x' | bash"),
                       ("pwsh", "& $tool x; 'Remove-Item'")],
             evidence=E("argued", "whole-raw-line scan at the exact point where the structured reading "
                        "gives up; fixes the hookproto.py:27 fallback")),
        dict(id="guard.encoded", when=("always",), **{"class": DENY_CLS}, synthetic=True, override=False,
             message="powershell -EncodedCommand carries a payload the guard could not decode.",
             use=[("*", "dz safedel --yes <path>")],
             examples=[("bash", "powershell -EncodedCommand !!!notbase64!!!")],
             evidence=E("argued", "an unreadable payload is refused, not guessed at")),
    ]),
]

# Wrappers: commands whose argument is itself a command or a program.  The judge looks INSIDE
# them (quote-aware), instead of guessing from stripped text.
WRAPPERS = [
    dict(names=("bash", "sh", "zsh", "dash", "ksh", "ash"), unwrap="shell"),
    dict(names=("cmd",), unwrap="cmd"),
    dict(names=("powershell", "pwsh"), unwrap="ps"),
    dict(names=("python", "py", "pypy"), unwrap="interp", lang="python", cflags=("-c",)),
    dict(names=("node", "nodejs", "bun", "deno"), unwrap="interp", lang="node",
         cflags=("-e", "--eval", "-p", "--print")),
    dict(names=("perl",), unwrap="interp", lang="perl", cflags=("-e", "-E")),
    dict(names=("ruby",), unwrap="interp", lang="ruby", cflags=("-e",)),
    dict(names=("php",), unwrap="interp", lang="php", cflags=("-r",)),
    dict(names=("eval",), unwrap="eval"),
    dict(names=("xargs", "parallel"), unwrap="inner"),
    dict(names=("sudo", "doas", "env", "command", "builtin", "exec", "nohup", "time", "nice", "ionice",
                "stdbuf", "timeout", "winpty", "watch", "start", "wsl"), unwrap="prefix"),
    dict(names=("ssh",), unwrap="prefix", skip=1),
]

_KEYWORDS_SKIP = {
    "bash": {"if", "then", "elif", "else", "do", "while", "until", "!", "{", "time"},
    "pwsh": {"if", "elseif", "else", "try", "catch", "finally", "do", "while", "foreach", "for", "switch"},
    "cmd": {"if", "else", "do"},
}
_KEYWORDS_END = {"fi", "done", "esac", "}", "then", "else", "do", "{"}
_CD = {
    "bash": ("cd", "pushd"),
    "pwsh": ("cd", "chdir", "sl", "set-location", "pushd", "push-location"),
    "cmd": ("cd", "chdir", "pushd"),
}
_NULL_DEVICES = ("/dev/null", "nul", "nul:", "$null")

_FORMS = {}
_TOOLS = {"bash": {}, "pwsh": {}, "cmd": {}}
_TOOL_RE = {"bash": [], "pwsh": [], "cmd": []}
_WRAP = {}


def _build_indexes():
    _FORMS.clear()
    for sh in _TOOLS:
        _TOOLS[sh].clear()
        del _TOOL_RE[sh][:]
    for entry in REGISTRY:
        for form in entry["forms"]:
            form["tool"] = entry["tool"]
            _FORMS[form["id"]] = form
        for sh, names in entry.get("names", {}).items():
            for nm in names:
                _TOOLS[sh][nm] = entry
        for sh, rx in entry.get("name_re", {}).items():
            _TOOL_RE[sh].append((re.compile(rx), entry))
    _WRAP.clear()
    for w in WRAPPERS:
        for nm in w["names"]:
            _WRAP[nm] = w


_build_indexes()


def iter_forms():
    """(tool entry, form) for every form in the registry, in table order."""
    for entry in REGISTRY:
        for form in entry["forms"]:
            yield entry, form


# ----------------------------------------------------------------------------
# 5. classifying one command
# ----------------------------------------------------------------------------


class Step(object):
    """A registry form matched on the line."""
    __slots__ = ("form", "detail", "notes")

    def __init__(self, form, detail="", notes=None):
        self.form = form
        self.detail = detail
        self.notes = notes or {}

    @property
    def id(self):
        return self.form["id"]

    @property
    def cls(self):
        return self.form["class"]


class Cmd(object):
    """One command occurrence anywhere on the line (top level, inside $( ), inside a wrapper)."""
    __slots__ = ("name", "text", "step", "join", "after", "list_id", "kind", "cd", "nullout", "opaque")

    def __init__(self, name, text, list_id, kind, join):
        self.name = name
        self.text = text
        self.step = None
        self.join = join        # separator linking it to the previous command of its list (None = first)
        self.after = []         # separators after it, up to the next command or the end of its list
        self.list_id = list_id
        self.kind = kind        # 'top' | 'subst' | 'wrap'
        self.cd = None          # literal directory when this is `cd <literal>`
        self.nullout = False    # output redirected to the null device
        self.opaque = False     # command name came from an expansion / piped into a shell


class _Acc(object):
    def __init__(self, raw):
        self.raw = raw
        self.cmds = []
        self.lists = 0
        self.doubt = []
        self.suspects = []
        self.depth = 0
        self.assign = {}        # NAME -> literal value assigned earlier on this line


def _cmdname(word):
    base = re.sub(r"^.*[\\/]", "", word).lower()
    base = re.sub(r"\.(exe|cmd|bat|com)$", "", base)
    if re.match(r"^python[\d.]*$", base) or re.match(r"^pypy[\d.]*$", base):
        base = "python"
    return base


def _join(seps):
    if not seps:
        return ";"
    for sp in seps:
        if sp != "&&":
            return sp
    return "&&"


def _fake_toks(words):
    out = []
    for w in words:
        t = Tok(w)
        t.quoted = True
        out.append(t)
    return out


def _prefix_scan(args, shell, skip=0):
    """Index of the command word after a transparent prefix (sudo -u bob rm ..., timeout 5 rm ...)."""
    i, skipped, prev_opt = 0, 0, False
    while i < len(args):
        x = args[i].text
        if x == "--":
            return i + 1 if i + 1 < len(args) else None
        if x.startswith("-") and len(x) > 1:
            prev_opt = True
            i += 1
            continue
        if re.match(r"^[A-Za-z_]\w*=", x) or re.match(r"^[\d.]+[smhd]?$", x):
            prev_opt = False
            i += 1
            continue
        nm = _cmdname(x)
        if nm in _TOOLS[shell] or nm in _WRAP:
            return i
        if prev_opt:
            prev_opt = False
            i += 1
            continue
        if skipped < skip:
            skipped += 1
            i += 1
            continue
        return i
    return None


def _inner_classes(words, shell, ctx, depth):
    """Classes found when `words` is read as one command (for find -exec, xargs)."""
    tmp = _Acc(" ".join(words))
    tmp.depth = depth + 1
    _seg_commands(Seg(_fake_toks(words)), shell, ctx, tmp, 0, "wrap", None)
    return set(c.step.cls for c in tmp.cmds if c.step)


def _match(entry, toks, shell, ctx, depth):
    argv = [t.text for t in toks[1:]]
    if entry.get("name_re"):
        argv = [toks[0].text] + argv
    notes = {}
    if entry.get("pre"):
        argv, notes = PRE[entry["pre"]](argv)
    env = _Env(shell, ctx, lambda words: _inner_classes(words, shell, ctx, depth))
    for form in entry["forms"]:
        if form.get("synthetic"):
            continue
        if "shells" in form and shell not in form["shells"]:
            continue
        if _eval(form["when"], argv, env):
            return Step(form, notes=notes)
    return None


def _payload(text, dialect, ctx, A, kind, join):
    """Read `text` as a command list in `dialect` and add its commands.  Returns how many."""
    if A.depth > 6:
        A.doubt.append("wrappers nested too deep")
        return 0
    before = len(A.cmds)
    lx = _Lexer(text, dialect)
    items = lx.run()
    A.doubt.extend(lx.doubt)
    A.depth += 1
    _analyse(items, dialect, ctx, A, kind, join)
    A.depth -= 1
    return len(A.cmds) - before


def _stdin_bodies(seg):
    return [b for b, _q in seg.heredocs] + [t.text for op, t in seg.redirs if op == "<<<"]


def _add(A, name, toks, lid, kind, join, step=None, opaque=False):
    c = Cmd(name, " ".join(t.text for t in toks), lid, kind, join)
    c.step = step
    c.opaque = opaque
    A.cmds.append(c)
    return c


def _unwrap(w, name, toks, seg, shell, ctx, A, lid, kind, join):
    """Look inside a wrapper.  True when it added the commands that stand for this segment."""
    how, args = w["unwrap"], toks[1:]

    if how == "shell":
        k = next((i for i, t in enumerate(args) if re.match(r"^-[A-Za-z]*c[A-Za-z]*$", t.text)), None)
        if k is not None and k + 1 < len(args):
            return _payload(args[k + 1].text, "bash", ctx, A, "wrap", join) > 0
        script = next((t for t in args if not t.text.startswith("-")), None)
        if script is not None:
            return False                               # a script file: blind spot, plain command
        bodies = _stdin_bodies(seg)
        if bodies:
            return sum(_payload(b, "bash", ctx, A, "wrap", join) for b in bodies) > 0
        if join in ("|", "|&"):
            _add(A, name, toks, lid, kind, join, opaque=True)      # text piped into a shell
            return True
        return False

    if how == "cmd":
        k = next((i for i, t in enumerate(args) if re.match(r"(?i)^/+[ckr]$", t.text)), None)
        if k is not None and k + 1 < len(args):
            return _payload(" ".join(t.text for t in args[k + 1:]), "cmd", ctx, A, "wrap", join) > 0
        return False

    if how == "ps":
        for i, t in enumerate(args):
            low = t.text.lower()
            if low in ("-e", "-ec") or (len(low) > 2 and ps_param(low, "encodedcommand", 2)):
                raw = args[i + 1].text if i + 1 < len(args) else ""
                try:
                    text = base64.b64decode(raw, validate=True).decode("utf-16-le")
                except Exception:
                    _add(A, name, toks, lid, kind, join, step=Step(_FORMS["guard.encoded"]))
                    return True
                return _payload(text, "pwsh", ctx, A, "wrap", join) > 0
            if ps_param(low, "command"):
                rest = args[i + 1:]
                if rest and rest[0].text != "-":
                    return _payload(" ".join(x.text for x in rest), "pwsh", ctx, A, "wrap", join) > 0
                break
            if ps_param(low, "file"):
                return False
        bodies = _stdin_bodies(seg)
        if bodies:
            return sum(_payload(b, "pwsh", ctx, A, "wrap", join) for b in bodies) > 0
        pos = [t for t in args if not t.text.startswith("-")]
        if pos and (len(pos) > 1 or " " in pos[0].text):
            return _payload(" ".join(x.text for x in pos), "pwsh", ctx, A, "wrap", join) > 0
        return False

    if how == "interp":
        cflags = w["cflags"]
        bodies = []
        for i, t in enumerate(args):
            if t.text in cflags or (name == "python" and re.match(r"^-[A-Za-z]*c$", t.text)):
                if i + 1 < len(args):
                    bodies.append(args[i + 1].text)
                break
        if not bodies and not any(not t.text.startswith("-") for t in args):
            bodies = _stdin_bodies(seg)
        elif not bodies and any(t.text == "-" for t in args):
            bodies = _stdin_bodies(seg)
        for b in bodies:
            if CODE_DELETE[w["lang"]].search(b) or (CODE_SHELLOUT.search(b) and CODE_SHELL_DELETE.search(b)):
                _add(A, name, toks, lid, kind, join, step=Step(_FORMS["interp.delete"], detail=name))
                return True
        return False

    if how == "eval":
        if args:
            return _payload(" ".join(t.text for t in args), "bash", ctx, A, "wrap", join) > 0
        return False

    if how == "inner":
        k = _prefix_scan(args, shell)
        if k is not None:
            tmp = _Acc(A.raw)
            tmp.depth = A.depth + 1
            _seg_commands(Seg(args[k:]), shell, ctx, tmp, 0, "wrap", None)
            A.doubt.extend(tmp.doubt)
            hit = [c for c in tmp.cmds if c.step and c.step.cls in (UNCOND, DENY_CLS, STAGING)]
            if hit:
                _add(A, name, toks, lid, kind, join, step=Step(_FORMS["xargs.delete"], detail=hit[0].step.id))
                return True
        return False

    if how == "prefix":
        k = _prefix_scan(args, shell, w.get("skip", 0))
        if k is None:
            return False
        rest = args[k:]
        if len(rest) == 1 and rest[0].quoted and re.search(r"\s", rest[0].text):
            return _payload(rest[0].text, "bash", ctx, A, "wrap", join) > 0   # ssh host 'a; b'
        inner = Seg(rest)
        inner.redirs, inner.heredocs = seg.redirs, seg.heredocs
        before = len(A.cmds)
        _seg_commands(inner, shell, ctx, A, lid, kind, join)
        return len(A.cmds) > before

    return False


def _seg_commands(seg, shell, ctx, A, lid, kind, join):
    """Add the command(s) one segment stands for."""
    toks = list(seg.toks)
    assigned = False
    skip = _KEYWORDS_SKIP[shell]
    while toks:
        x = toks[0]
        if not x.quoted and x.text in skip:
            toks = toks[1:]
            continue
        m = re.match(r"^([A-Za-z_]\w*)(\[[^\]]*\])?\+?=(.*)$", x.text, re.S) if shell == "bash" else None
        if m:
            assigned = True                              # NAME=value  (prefix or standalone)
            if x.literal and not x.subs and not x.glob:
                A.assign[m.group(1)] = m.group(3)
            else:
                A.assign.pop(m.group(1), None)
            toks = toks[1:]
            continue
        if shell == "pwsh" and len(toks) >= 2 and x.text.startswith("$") and not toks[1].quoted \
                and toks[1].text in ("=", "+="):
            assigned = True                              # $x = <command ...>
            nm = x.text[1:].split(":")[-1]
            if len(toks) == 3 and toks[2].quoted and toks[2].literal:
                A.assign[nm] = toks[2].text
            else:
                A.assign.pop(nm, None)
            toks = toks[2:]
            continue
        break
    if not toks:
        if assigned:
            _add(A, "(assignment)", seg.toks, lid, kind, join)
        return
    first = toks[0]
    if len(toks) == 1 and not first.quoted and first.text in _KEYWORDS_END:
        return
    nullout = any(op[:1] in (">", "&") and t.text.lower() in _NULL_DEVICES for op, t in seg.redirs)
    before = len(A.cmds)

    # pwsh: a statement that starts with a string literal is output ("x = $x"), not a command,
    # unless the call operator ran it (& "path").
    if shell == "pwsh" and first.quoted and not seg.call and not re.search(r"[\\/][\w.+-]+$", first.text):
        _add(A, "(expression)", toks, lid, kind, join).nullout = nullout
        return

    # A command NAME that is (or ends in) an expansion.  Resolve it when the value was assigned as a
    # literal earlier on this very line (X=rm; $X -rf d); otherwise the guard cannot know what runs.
    if not first.literal:
        m = re.match(r"^\$\{?([A-Za-z_]\w*)\}?$", first.text)
        tail = re.search(r"[\\/]([\w.+-]+)$", first.text)
        if m and A.assign.get(m.group(1), "").strip():
            toks = _fake_toks(A.assign[m.group(1)].split()) + toks[1:]
            first = toks[0]
        elif tail:
            pass                                         # "$VENV/bin/python": the name is the last component
        elif shell == "pwsh" and first.text[:1] in ("$", "@") and not seg.call:
            entry = next((e for rx, e in _TOOL_RE[shell] if rx.search(first.text)), None)
            step = _match(entry, toks, shell, ctx, A.depth) if entry is not None else None
            _add(A, "(expression)", toks, lid, kind, join, step=step).nullout = nullout
            return                                       # a PowerShell expression statement, not a command
        else:
            _add(A, "(expansion)", toks, lid, kind, join, opaque=True).nullout = nullout
            return
    name = _cmdname(first.text)

    if name in _CD[shell]:
        c = _add(A, name, toks, lid, kind, join)
        args = [t for t in toks[1:] if not (shell == "cmd" and t.text.lower() == "/d")
                and not (shell == "pwsh" and t.text.lower() in ("-path", "-literalpath"))
                and t.text != "--"]
        if len(args) == 1 and args[0].literal and not args[0].glob and not args[0].subs \
                and args[0].text not in ("-", "") and not seg.redirs:
            c.cd = args[0].text
        return

    w = _WRAP.get(name)
    if w is not None and _unwrap(w, name, toks, seg, shell, ctx, A, lid, kind, join):
        for c in A.cmds[before:]:
            c.nullout = c.nullout or nullout
        return

    entry = _TOOLS[shell].get(name)
    if entry is None:
        for rx, e in _TOOL_RE[shell]:
            if rx.search(first.text):
                entry = e
                break
    step = _match(entry, toks, shell, ctx, A.depth) if entry is not None else None
    c = _add(A, name, toks, lid, kind, join, step=step)
    c.nullout = nullout
    if step is None and (SUSPECT_NAME.search(name) or any(
            not t.quoted and SUSPECT_ARG.search(t.text) for t in toks[1:4])):
        A.suspects.append(c.text[:200])


def _analyse(items, shell, ctx, A, kind, first_join=None):
    """Turn one lexed command list into Cmd objects (recursing into substitutions)."""
    A.lists += 1
    lid = A.lists
    seps, first, last = [], True, None
    for it in items:
        if isinstance(it, str):
            if it not in _GROUP:
                seps.append(it)
            continue
        for t in it.toks + [r[1] for r in it.redirs]:
            for sub in t.subs:
                _analyse(sub, shell, ctx, A, "subst")
        for body, quoted in it.heredocs:           # unquoted heredoc bodies expand $( ) too
            if not quoted and ("$(" in body or "`" in body) and SUSPECT.search(body):
                A.doubt.append("command substitution inside an unquoted heredoc")
        before = len(A.cmds)
        join = (first_join if first else _join(seps))
        _seg_commands(it, shell, ctx, A, lid, kind, join)
        if len(A.cmds) == before:
            continue
        if last is not None:
            last.after = list(seps)
        last, first, seps = A.cmds[-1], False, []
    if last is not None:
        last.after = list(seps)


# ----------------------------------------------------------------------------
# 6. chain rules and judge()
# ----------------------------------------------------------------------------

RULES = {
    "R0": ("unreadable line with a deletion word",
           "The guard fails closed: when it cannot read a line that mentions a deletion, it refuses it."),
    "R1": ("a form that is always refused",
           "This form is refused even on its own, because a recoverable way to do the same thing exists "
           "or because it has done damage its name does not suggest."),
    "R2": ("more than one deletion in one call",
           "Each deletion gets its own call, so its result can be read before the next one runs. A chain "
           "assumes the earlier steps worked."),
    "R3": ("an unconditional deletion must be the only command in the call",
           "A deletion that asks no questions must not share a call with anything else: no ';', no pipe, "
           "no '||', no '&&' chain, no $( ), no '&', no output sent to the null device. The single "
           "exception is a leading 'cd <literal path> &&'."),
    "R4": ("a staging step may be joined only with &&",
           "dz safedel may share a call only through '&&' (so a refusal stops the chain). It is never "
           "piped (a pipe replaces its exit code and trims its output), never joined with ';' or '||', "
           "never run in the background, never fed from $( ), and its output is never thrown away."),
    "R5": ("self-checking steps do not count as deletions", ""),
}


class Verdict(object):
    """The judge's answer.  `steps` lists every registered form seen, in line order."""
    __slots__ = ("decision", "rule", "why", "culprit", "steps", "post", "cd_prefix", "suspects",
                 "doubt", "shell", "line", "ask")

    def __init__(self, decision, shell, line):
        self.decision = decision
        self.rule = None
        self.why = ""
        self.culprit = None     # the Step the refusal is about
        self.steps = []
        self.post = None        # post-condition kind to run after an allowed deletion
        self.cd_prefix = []     # literal `cd` targets in front of the deletion (for resolving its paths)
        self.suspects = []
        self.doubt = []
        self.shell = shell
        self.line = line
        self.ask = False

    @property
    def denied(self):
        return self.decision == DENY

    @property
    def ids(self):
        return [s.id for s in self.steps]

    def as_dict(self):
        return {
            "decision": self.decision, "rule": self.rule, "why": self.why,
            "culprit": self.culprit.id if self.culprit else None,
            "forms": self.ids, "classes": [s.cls for s in self.steps], "post": self.post,
            "cd_prefix": self.cd_prefix, "suspects": self.suspects, "doubt": self.doubt, "shell": self.shell,
        }

    def __repr__(self):
        return "<Verdict %s %s %s>" % (self.decision, self.rule or "-", ",".join(self.ids))


def _deny(v, rule, why, culprit):
    v.decision, v.rule, v.why, v.culprit = DENY, rule, why, culprit
    return v


def judge(cmd, shell="bash", ctx=None):
    """Pure: (command text, shell dialect, context) -> Verdict.  Executes nothing."""
    if shell not in _TOOLS:
        raise ValueError("unknown shell dialect %r (bash, pwsh, cmd)" % (shell,))
    c = dict(DEFAULT_CTX)
    c.update(ctx or {})
    A = _Acc(cmd)
    lx = _Lexer(cmd, shell)
    items = lx.run()
    A.doubt.extend(lx.doubt)
    _analyse(items, shell, c, A, "top")

    v = Verdict(ALLOW, shell, cmd)
    v.steps = [x.step for x in A.cmds if x.step]
    v.suspects = A.suspects
    v.doubt = A.doubt

    # R0 -- fail closed.  This is the whole-raw-line scan: it looks at the complete original text,
    # never at a stripped or re-split copy (the hookproto.py:27 defect).
    if A.doubt and SUSPECT.search(cmd):
        return _deny(v, "R0", "could not read the line: " + "; ".join(sorted(set(A.doubt))), Step(_FORMS["guard.doubt"]))
    _rules(v, A)
    # R0 again, after the structured rules (their refusals teach more): a command the guard cannot
    # name, on a line that mentions a deletion anywhere in its raw text.
    if v.decision != DENY and any(x.opaque for x in A.cmds) and SUSPECT.search(cmd):
        v.post, v.ask, v.cd_prefix = None, False, []
        return _deny(v, "R0", "a command name comes from an expansion the guard cannot resolve, or from "
                              "text piped into a shell: '%s'" % next(x.text[:60] for x in A.cmds if x.opaque),
                     Step(_FORMS["guard.opaque"]))
    return v


def _rules(v, A):
    """R1-R5 over the commands found on the line.  Mutates and returns v."""
    # R1 -- always-deny forms
    for x in A.cmds:
        if x.step and x.step.cls == DENY_CLS:
            return _deny(v, "R1", "'%s' is refused even when it is the only command" % x.text[:120], x.step)

    hard = [x for x in A.cmds if x.step and x.step.cls in (UNCOND, STAGING)]
    if not hard:
        return v                                                   # R5: nothing that counts as a deletion

    # R2 -- one deletion per call
    if len(hard) > 1:
        return _deny(v, "R2", "%d deletion steps on one line: %s" % (
            len(hard), "; ".join("%s [%s]" % (x.text[:60], x.step.id) for x in hard)), hard[0].step)

    h = hard[0]
    others = [x for x in A.cmds if x is not h]
    strict = h.step.cls == UNCOND or h.step.form.get("sole")
    rule = "R3" if strict else "R4"
    if h.kind == "subst":
        return _deny(v, rule, "the deletion runs inside a command substitution, where its exit code and "
                              "output are swallowed", h.step)
    if h.nullout:
        return _deny(v, rule, "the deletion's output is sent to the null device, so its result cannot be read", h.step)
    if "&" in h.after:
        return _deny(v, rule, "the deletion is sent to the background with '&'", h.step)

    if strict:
        if others:
            sole = h.step.form.get("sole")
            cd_ok = (not sole and A.cmds[-1] is h
                     and all(x.cd is not None and x.list_id == h.list_id for x in others)
                     and all(x.join == "&&" for x in A.cmds[1:]))
            if not cd_ok:
                bad = next((x for x in others if x.cd is None or x.list_id != h.list_id), others[0])
                how = "a command substitution" if bad.kind == "subst" else "'%s'" % bad.text[:60]
                if sole:
                    why = "'%s' must be the only command in the call, but it shares it with %s" % (h.text[:60], how)
                else:
                    why = ("'%s' shares the call with %s; only 'cd <literal path> &&' may come before an "
                           "unconditional deletion, and nothing may follow it" % (h.text[:60], how))
                return _deny(v, "R3", why, h.step)
            v.cd_prefix = [x.cd for x in others]
        v.post = h.step.form.get("post")
        if h.step.form.get("ask"):
            v.decision, v.ask = ASK, True
            v.culprit = h.step
            v.why = "needs a human's confirmation"
            return v
        v.decision = ALLOW_POST
        return v

    # staging (R4)
    sub = next((x for x in others if x.kind == "subst"), None)
    if sub is not None:
        return _deny(v, "R4", "an argument of the staging step comes from a command substitution "
                              "('%s'); resolve the path in its own call and pass it literally" % sub.text[:60], h.step)
    for x in A.cmds:
        if x.join not in (None, "&&"):
            return _deny(v, "R4", "the staging step shares the call with %r; only '&&' may join it" % (
                x.join.replace("\n", "newline"),), h.step)
    v.cd_prefix = [x.cd for x in others if x.cd is not None]
    v.post = h.step.form.get("post")
    v.decision = ALLOW_POST
    return v


# ----------------------------------------------------------------------------
# 7. refusal messages
# ----------------------------------------------------------------------------

USE_HEADER = "Do this instead (one call each; read the result before the next):"
OVERRIDE_HINT = "If the unrecoverable form is truly what you need: dz guarded -- <the command>   (prints the preview; a human confirms the run)"
_PLACEHOLDER = re.compile(r"<([A-Za-z][\w-]*)>")


def use_lines(form, shell):
    """The safe commands a form recommends for this shell."""
    return [c for sh, c in form.get("use", ()) if sh in ("*", shell)]


def render(v):
    """The text shown to the caller.  Its LAST line is the verdict line."""
    if v.decision in (ALLOW, ALLOW_POST):
        detail = (v.ids[0] + " post=" + str(v.post)) if v.decision == ALLOW_POST else ""
        return verdict_line("guard", v.decision, detail)
    form = v.culprit.form
    out = []
    if v.decision == ASK:
        out.append("CONFIRMATION NEEDED by the dz deletion guard: %s" % form["id"])
    else:
        out.append("BLOCKED by the dz deletion guard -- rule %s: %s" % (v.rule, RULES[v.rule][0]))
        out.append("What was found: %s" % v.why)
        out.append("Why: %s" % RULES[v.rule][1])
    if form.get("message"):
        out.append("About %s [%s, %s]: %s" % (form["tool"], form["id"], form["class"], form["message"]))
    lines = use_lines(form, v.shell)
    if v.decision == DENY and v.rule in ("R2", "R3", "R4") and form["class"] in (UNCOND, STAGING):
        lines = ["<the one deletion step, alone>"] + lines if not lines else lines
    if lines:
        out.append(USE_HEADER)
        out.extend("  " + ln for ln in lines)
    if v.decision == DENY and form["class"] in (DENY_CLS, UNCOND) and form.get("override", True) \
            and not form.get("synthetic") and form["id"] != "guarded.run":
        out.append(OVERRIDE_HINT)
    out.append(verdict_line("guard", "ASK" if v.decision == ASK else "BLOCKED",
                            "%s %s" % (v.rule or "-", form["id"])))
    return "\n".join(out)


def recommended(text):
    """Commands a rendered message tells the caller to run (for the round-trip test)."""
    out, on = [], False
    for ln in text.splitlines():
        if ln == USE_HEADER:
            on = True
            continue
        if on and ln.startswith("  "):
            out.append(ln[2:])
        else:
            on = False
    return out


def fill(command):
    """Replace <placeholders> with plain words so a recommended command can be judged."""
    return _PLACEHOLDER.sub(lambda m: "X_" + m.group(1).replace("-", "_"), command)


# ----------------------------------------------------------------------------
# registry self-check (used by the tests and by --check)
# ----------------------------------------------------------------------------

REQUIRED_ALWAYS = ("id", "when", "class", "examples", "evidence")
EVIDENCE_LEVELS = ("measured", "documented", "argued")


def _when_names(expr, out):
    out.append(expr[0])
    if expr[0] in COMBINATORS:
        for e in expr[1:]:
            _when_names(e, out)
    return out


def validate_registry():
    """Problems with the table itself, as strings.  Empty list = sound."""
    bad, seen = [], set()
    for entry, form in iter_forms():
        fid = form.get("id", "?")
        for k in REQUIRED_ALWAYS:
            if not form.get(k):
                bad.append("%s: missing %s" % (fid, k))
        if fid in seen:
            bad.append("%s: duplicate id" % fid)
        seen.add(fid)
        cls = form.get("class")
        if cls not in CLASSES:
            bad.append("%s: unknown class %r" % (fid, cls))
        for nm in _when_names(form.get("when", ("always",)), []):
            if nm not in PREDICATES and nm not in COMBINATORS:
                bad.append("%s: unknown predicate %r" % (fid, nm))
        ev = form.get("evidence") or {}
        if ev.get("level") not in EVIDENCE_LEVELS or not ev.get("what") or not ev.get("date"):
            bad.append("%s: evidence needs level/what/date" % fid)
        if cls == SELF and ev.get("level") != "measured":
            bad.append("%s: self-checking requires MEASURED evidence" % fid)
        if cls in (UNCOND, DENY_CLS, STAGING):
            if not form.get("message"):
                bad.append("%s: %s form needs a message" % (fid, cls))
            if not form.get("use"):
                bad.append("%s: %s form needs a `use` (the safe way)" % (fid, cls))
        if cls in (UNCOND, DENY_CLS, STAGING) and not form.get("synthetic"):
            if form.get("post") not in POST_KINDS:
                bad.append("%s: %s form needs a post-condition kind from POST_KINDS" % (fid, cls))
            pv = form.get("preview")
            if not (isinstance(pv, tuple) and len(pv) == 2 and pv[0] in (PREVIEW_NATIVE, PREVIEW_COMPUTED) and pv[1]):
                bad.append("%s: %s form needs a preview (native|computed, how)" % (fid, cls))
        for sh, _line in form.get("examples", ()):
            if sh not in _TOOLS:
                bad.append("%s: example names unknown shell %r" % (fid, sh))
        for sh, _c in form.get("use", ()):
            if sh != "*" and sh not in _TOOLS:
                bad.append("%s: use names unknown shell %r" % (fid, sh))
    return bad


# ----------------------------------------------------------------------------
# 8. main(): hook mode and explain mode
# ----------------------------------------------------------------------------

TOOL_SHELL = {"Bash": "bash", "PowerShell": "pwsh"}
UNATTENDED_MODES = ("bypassPermissions", "dontAsk")     # UNVERIFIED names -- see the canary procedure


def default_log_path(env):
    root = env.get("CLAUDE_USER_DIR") or os.path.join(os.path.expanduser("~"), "claude")
    return os.path.join(root, "logs", "dz-guard", "guard.jsonl")


def _log(env, record):
    """Append one JSON line.  A logging failure never changes a verdict."""
    path = env.get("DZ_GUARD_LOG")
    if path is None:
        path = default_log_path(env)
    if path in ("", "off", "0"):
        return
    try:
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    except Exception:
        pass


def hook(stdin_text, env, out, err):
    """Claude Code PreToolUse hook.  Returns the process exit code: 0 allow, 2 block."""
    if env.get("DZ_GUARD", "").lower() in ("off", "0"):
        return HOOK_ALLOW        # the hook's environment is the harness's, not the command's
    try:
        data = json.loads(stdin_text)
        shell = TOOL_SHELL.get(data.get("tool_name"))
        if shell is None:
            return HOOK_ALLOW
        cmd = (data.get("tool_input") or {}).get("command")
        if not isinstance(cmd, str):
            raise ValueError("no command text in tool_input")
    except Exception as exc:
        if SUSPECT.search(stdin_text or ""):     # fail closed, but only when a deletion is in sight
            err.write("BLOCKED by the dz deletion guard: unreadable hook input (%s) containing a deletion "
                      "word.\n%s\n" % (exc, verdict_line("guard", "BLOCKED", "R0 hook-input")))
            return HOOK_BLOCK
        return HOOK_ALLOW

    ctx = {"agent": True,
           "unattended": env.get("DZ_GUARD_UNATTENDED") == "1" or data.get("permission_mode") in UNATTENDED_MODES}
    t0 = time.time()
    try:
        v = judge(cmd, shell, ctx)
    except Exception as exc:                      # a bug in the judge must not open the gate
        if SUSPECT.search(cmd):
            err.write("BLOCKED by the dz deletion guard: internal error (%s: %s) on a line containing a "
                      "deletion word. Run the deletion as its own plain command.\n%s\n"
                      % (type(exc).__name__, exc, verdict_line("guard", "BLOCKED", "R0 internal-error")))
            _log(env, {"ts": time.time(), "decision": DENY, "rule": "R0", "error": repr(exc), "cmd": cmd[:500]})
            return HOOK_BLOCK
        return HOOK_ALLOW

    if v.decision != ALLOW or v.suspects:
        rec = v.as_dict()
        rec.update({"ts": round(time.time(), 3), "ms": round((time.time() - t0) * 1000, 2),
                    "cmd": cmd[:500], "session": data.get("session_id"), "v": GUARD_VERSION})
        _log(env, rec)

    if v.decision == DENY:
        err.write(render(v) + "\n")
        return HOOK_BLOCK
    if v.decision == ASK:
        if ctx["unattended"]:
            err.write(render(v).replace("CONFIRMATION NEEDED", "BLOCKED (no human present to confirm)")
                      .replace("guard: ASK", "guard: BLOCKED") + "\n")
            return HOOK_BLOCK
        out.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "ask",
            "permissionDecisionReason": render(v)}}) + "\n")
        return HOOK_ALLOW
    return HOOK_ALLOW


def main(argv=None, stdin=None, stdout=None, stderr=None, env=None):
    """No arguments: hook mode.  --judge LINE [--shell S] [--human]: explain.  --check / --table."""
    argv = list(sys.argv[1:] if argv is None else argv)
    stdin = sys.stdin if stdin is None else stdin
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    env = os.environ if env is None else env

    if not argv:
        return hook(stdin.read(), env, out, err)

    if argv[0] == "--check":
        bad = validate_registry()
        for b in bad:
            out.write(b + "\n")
        out.write(verdict_line("guard", "CHECK", "%d problem(s), %d forms" % (len(bad), len(_FORMS))) + "\n")
        return EXIT_OK if not bad else EXIT_ERROR

    if argv[0] == "--table":
        for entry, form in iter_forms():
            out.write("%-28s %-16s %-10s %s\n" % (form["id"], form["class"], form["evidence"]["level"], entry["tool"]))
        return EXIT_OK

    if argv[0] == "--judge":
        shell, human, line = "bash", False, None
        rest = argv[1:]
        while rest:
            a = rest.pop(0)
            if a == "--shell" and rest:
                shell = rest.pop(0)
            elif a == "--human":
                human = True
            elif line is None:
                line = a
            else:
                err.write("usage: _guard.py --judge LINE [--shell bash|pwsh|cmd] [--human]\n")
                return EXIT_USAGE
        if line is None or shell not in _TOOLS:
            err.write("usage: _guard.py --judge LINE [--shell bash|pwsh|cmd] [--human]\n")
            return EXIT_USAGE
        v = judge(line, shell, {"agent": not human})
        out.write(render(v) + "\n")
        return EXIT_BLOCKED if v.decision == DENY else EXIT_OK

    err.write("usage: _guard.py            (hook: JSON on stdin)\n"
              "       _guard.py --judge LINE [--shell bash|pwsh|cmd] [--human]\n"
              "       _guard.py --check | --table\n")
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
