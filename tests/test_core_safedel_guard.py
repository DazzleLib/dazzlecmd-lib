"""Tests for the registry-v1 prototype (_guard.py).

Critical Safety Rule 1b: every line below is DATA.  The judge is a pure function from text to a
verdict; nothing here runs a shell, and an autouse fixture makes every route to a process or a
deletion raise, so a mutated judge can only change a verdict -- it cannot delete anything.

    python -m pytest test_guard.py -q
"""
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
# Moved from tests/one-offs/thinking/safedel-line-guard/registry-v1/ on 2026-11-19 (copied
# byte for byte); the module now lives in the library.
GUARD_PATH = os.path.join(HERE, os.pardir, "dazzlecmd_lib", "core", "safedel", "_guard.py")

# loaded by path, the way the hook and the lib copy will be: proves it is standalone-loadable
_spec = importlib.util.spec_from_file_location("_guard_under_test", GUARD_PATH)
G = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(G)

A, P, K, D = G.ALLOW, G.ALLOW_POST, G.ASK, G.DENY


@pytest.fixture(autouse=True)
def no_side_effects(request, monkeypatch):
    """Rule 1b: make the dangerous entry points raise while a test body runs.

    Tests that use tmp_path are exempt (pytest itself creates and prunes those folders); they
    only ever touch files they made there.
    """
    if "tmp_path" in request.fixturenames:
        yield
        return

    def boom(*a, **k):
        raise AssertionError("a guard test tried to run a process or delete something")
    for mod, name in ((os, "system"), (os, "remove"), (os, "unlink"), (os, "rmdir"), (os, "popen"),
                      (shutil, "rmtree"), (subprocess, "Popen"), (subprocess, "run")):
        monkeypatch.setattr(mod, name, boom)
    yield


# The 2026-11-17 line, verbatim (postmortem + notes/safedel/2026-11-18__12-05-00).
INCIDENT = ('dz safedel "C:/code/filetoolkit__portable-key/private" 2>&1 | tail -1; dz safedel '
            '"C:/code/dazzle-preservelib__same-name-guard/private" 2>&1 | tail -1; ls /c/code/filetoolkit/github/private/ '
            '| head -2; ls /c/code/dazzle-preservelib/private/ | head -2; git -C /c/code/filetoolkit/github worktree '
            'remove C:/code/filetoolkit__portable-key && git -C /c/code/filetoolkit/github branch -d portable-key; git -C '
            '/c/code/dazzle-preservelib worktree remove --force C:/code/dazzle-preservelib__same-name-guard && git -C '
            '/c/code/dazzle-preservelib branch -d same-name-guard; git -C /c/code/filetoolkit/github worktree list; git -C '
            '/c/code/dazzle-preservelib worktree list')
# the same line as it would be written with --yes: still refused, by a different rule
INCIDENT_YES = INCIDENT.replace('dz safedel "', 'dz safedel --yes "')

COMMIT_HEREDOC = ("git commit -m \"$(cat <<'EOF'\nguard: remove rm; don't format (del) the tree\n\n"
                  "rm -rf is refused; git worktree remove too.\nEOF\n)\"")
PY_HEREDOC_DELETE = "python - <<'PY'\nimport os\nos.remove('a.db')\nPY"
PY_HEREDOC_SAFE = "python - <<'PY'\n# callers unlink what it returns; rm -rf is prose here\nprint('rmdir')\nPY"

# (shell, line, decision, rule or None, culprit/first form id or None)
TABLE = [
    # ---- the incident and the three hookproto.py defects
    ("bash", INCIDENT, D, "R1", "safedel.stage.noyes"),
    ("bash", INCIDENT_YES, D, "R1", "git.worktree.remove"),
    ("bash", "git -C /c/r worktree remove C:/w", D, "R1", "git.worktree.remove"),          # defect 1
    ("bash", 'bash -c "rm -rf x; echo hi"', D, "R3", "rm.recursive"),                      # defect 2 (:27)
    ("bash", "dz safedel --yes a 2>&1", P, None, "safedel.stage"),                         # defect 3
    ("bash", "echo $(rm x)", D, "R3", "rm.any"),
    ("bash", "python -c \"import shutil; shutil.rmtree('x')\"", D, "R1", "interp.delete"),
    ("bash", "git --no-pager -C /r worktree remove w", D, "R1", "git.worktree.remove"),
    ("bash", "git -c core.x=1 -C /r clean -fdx", D, "R1", "git.clean"),
    # ---- plan acceptance checks (P0)
    ("bash", "cd /x && dz safedel --yes a", P, None, "safedel.stage"),
    ("bash", "dz safedel --yes a && git worktree prune", P, None, "safedel.stage"),
    # ---- round-1 question 3: the five chain lines
    ("bash", "dz safedel --yes d/* && rmdir d", P, None, "safedel.stage"),
    ("bash", "dz safedel --yes x && git worktree prune", P, None, "safedel.stage"),
    ("bash", "dz safedel x; rm -rf x", D, "R1", "safedel.stage.noyes"),
    ("bash", "dz safedel --yes x; rm -rf x", D, "R2", None),
    ("bash", "rm -rf a && rm -rf b", D, "R2", "rm.recursive"),
    ("bash", "dz safedel --yes d/* ; rmdir d", D, "R4", "safedel.stage"),
    # ---- rule 5: self-checking steps are free
    ("bash", "rmdir a && rmdir b", A, None, None),
    ("bash", "rmdir a; rmdir b", A, None, None),
    ("bash", "git branch -d k && git branch -d j", A, None, None),
    ("bash", "git worktree prune", A, None, None),
    ("bash", "git worktree prune; rm -rf x", D, "R3", "rm.recursive"),
    # ---- rule 3 and its narrowed exemption
    ("bash", "rm -rf x", P, None, "rm.recursive"),
    ("bash", "cd build && rm -rf out", P, None, "rm.recursive"),
    ("bash", 'cd "C:/code/proj" && rm -rf out', P, None, "rm.recursive"),
    ("bash", "cd $D && rm -rf out", D, "R3", "rm.recursive"),
    ("bash", "cd build; rm -rf out", D, "R3", "rm.recursive"),
    ("bash", "ls && rm -rf out", D, "R3", "rm.recursive"),
    ("bash", "cd build && rm -rf out && ls", D, "R3", "rm.recursive"),
    ("bash", "rm -rf x || true", D, "R3", "rm.recursive"),
    ("bash", "rm -rf x 2>/dev/null", D, "R3", "rm.recursive"),
    ("bash", "rm -rf x &", D, "R3", "rm.recursive"),
    ("bash", "rm -rf x | tail -1", D, "R3", "rm.recursive"),
    ("bash", 'rm -rf "$(mktemp -d)"', D, "R3", "rm.recursive"),
    ("bash", "D=build; rm -rf \"$D\"", D, "R3", "rm.recursive"),
    ("bash", "if [ -d x ]; then rm -rf x; fi", D, "R3", "rm.recursive"),
    ("bash", "for d in a b; do rm -rf $d; done", D, "R3", "rm.recursive"),
    ("bash", "(rm -rf x)", P, None, "rm.recursive"),
    ("bash", "rm notes.txt", P, None, "rm.any"),
    ("bash", "/bin/rm -f a.tmp", P, None, "rm.any"),
    ("bash", "\\rm -rf x; ls", D, "R3", "rm.recursive"),
    ("bash", "sudo -u bob rm -rf x; ls", D, "R3", "rm.recursive"),
    ("bash", "timeout 5 rm -rf x && echo ok", D, "R3", "rm.recursive"),
    ("bash", "rm --help", A, None, None),
    # ---- rule 4: staging
    ("bash", "dz safedel --yes a; ls", D, "R4", "safedel.stage"),
    ("bash", "dz safedel --yes a | tail -1", D, "R4", "safedel.stage"),
    ("bash", "dz safedel --yes a >/dev/null 2>&1", D, "R4", "safedel.stage"),
    ("bash", 'dz safedel --yes "$(cat list.txt)"', D, "R4", "safedel.stage"),
    ("bash", 'dz safedel --yes "$TMPDIR/x"', P, None, "safedel.stage"),
    ("bash", "dz safedel --yes a\nls", D, "R4", "safedel.stage"),
    ("bash", "dz safedel --dry-run a; echo done", A, None, None),
    ("bash", "dz safedel list | head", A, None, None),
    ("bash", "dz safedel a", D, "R1", "safedel.stage.noyes"),
    ("bash", "dz safedel clean --yes", D, "R1", "safedel.clean"),
    ("bash", "dz safedel --yes delete-through-junction w/private", P, None, "safedel.stage.scoped"),
    ("bash", "cd w && dz safedel --yes delete-through-junction private", D, "R3", "safedel.stage.scoped"),
    # ---- dz guarded
    ("bash", "dz guarded --yes dangerous -- git worktree remove w", K, None, "guarded.run"),
    ("bash", "dz guarded --yes dangerous -- git clean -fd; ls", D, "R3", "guarded.run"),
    ("bash", "dz guarded -- git worktree remove w", A, None, None),
    # ---- wrappers and interpreters
    ("bash", 'bash -c "rm -rf x"', P, None, "rm.recursive"),
    ("bash", 'bash -lc "cd build && rm -rf out"', P, None, "rm.recursive"),
    ("bash", "sh -c 'dz safedel --yes a; rm -rf b'", D, "R2", None),
    ("bash", "eval 'rm -rf x; ls'", D, "R3", "rm.recursive"),
    ("bash", 'cmd //c "rd /s /q build"', P, None, "rmdir.cmd-recursive"),
    ("bash", 'cmd //c "rd build"', A, None, None),
    ("bash", "powershell -NoProfile -Command \"Remove-Item x -Recurse -Force; Test-Path x\"", D, "R3", "ri.recurse"),
    ("bash", "find . -name '*.pyc' | xargs rm", D, "R1", "xargs.delete"),
    ("bash", "find . -name x | xargs grep -l 'rm -rf'", A, None, None),
    ("bash", "find . -name '*.pyc' -delete", P, None, "find.delete"),
    ("bash", "find . -name x -exec grep -l rm {} +", A, None, None),
    ("bash", "find . -name '*.tmp' -exec rm {} \\;", P, None, "find.exec-delete"),
    ("bash", "python3 -c 'import os; os.remove(\"a\")'", D, "R1", "interp.delete"),
    ("bash", "python -c \"import os; os.rmdir('d')\"", A, None, None),          # self-checking call
    ("bash", "python -c \"print('rm -rf is bad')\"", A, None, None),
    ("bash", PY_HEREDOC_DELETE, D, "R1", "interp.delete"),
    ("bash", PY_HEREDOC_SAFE, A, None, None),
    ("bash", "bash <<'SH'\nrm -rf a\nrm -rf b\nSH", D, "R2", None),
    ("bash", "echo 'rm -rf x' | bash", D, "R0", "guard.opaque"),
    ("bash", "X='rm -rf'; $X build", D, "R3", "rm.recursive"),                  # resolved on the line
    ("bash", "$TOOL -rf build; echo rm", D, "R0", "guard.opaque"),
    ("bash", 'S=/c/tools/claude; "$S" --version; echo "rm it"', A, None, None),
    ("bash", '"$VENV/bin/python" -m pytest -k rm', A, None, None),
    ("bash", 'echo "rm -rf x', D, "R0", "guard.doubt"),
    ("bash", "powershell -EncodedCommand !!!", D, "R1", "guard.encoded"),
    # ---- git
    ("bash", "git clean -n", A, None, None),
    ("bash", "git clean -fd", D, "R1", "git.clean"),
    ("bash", "git reset --hard HEAD~1", P, None, "git.reset.hard"),
    ("bash", "git reset --soft HEAD~1", A, None, None),
    ("bash", "git stash && git reset --hard origin/main", D, "R3", "git.reset.hard"),
    ("bash", "git branch -D k", P, None, "git.branch.force-delete"),
    ("bash", "git branch -d k", A, None, None),
    ("bash", "git stash clear", P, None, "git.stash.drop"),
    ("bash", "git stash list", A, None, None),
    ("bash", "git push -f origin main", P, None, "git.push.force"),
    ("bash", "git push --force-with-lease origin main", P, None, "git.push.force"),
    ("bash", "git push origin main", A, None, None),
    ("bash", "git commit --amend --no-edit && git push --force", D, "R3", "git.push.force"),
    ("bash", "git checkout -- .", P, None, "git.checkout.discard"),
    ("bash", "git checkout main", A, None, None),
    ("bash", "git restore --staged a.py", A, None, None),
    ("bash", "git restore a.py", P, None, "git.restore.worktree"),
    # ---- rsync / robocopy
    ("bash", "rsync -a --delete a/ b/ && echo ok", D, "R3", "rsync.delete"),
    ("bash", "rsync -an --delete a/ b/", A, None, None),
    ("bash", "rsync -a a/ b/", A, None, None),
    ("bash", "robocopy a b //MIR", P, None, "robocopy.purge"),
    ("cmd", "robocopy a b /MIR /L", A, None, None),
    # ---- must NOT be refused (false-positive guards)
    ("bash", "ls | grep rm", A, None, None),
    ("bash", 'git commit -m "remove rm; use safedel"', A, None, None),
    ("bash", 'grep -rn "rm -rf" . ; ls', A, None, None),
    ("bash", "pytest -q 2>&1 | tail -3", A, None, None),
    ("bash", COMMIT_HEREDOC, A, None, None),
    ("bash", "cat > notes.md <<'EOF'\nrm -rf x; git worktree remove w\nEOF", A, None, None),
    ("bash", "echo \"don't rm\" # rm -rf x", A, None, None),
    ("bash", "cmake --build . --target clean && rmdir out", A, None, None),
    # ---- known blind spots: allowed, on purpose, and listed in the design
    ("bash", "truncate -s 0 important.db", A, None, None),
    ("bash", "mv a /dev/null", A, None, None),
    ("bash", "cp /dev/null important.db", A, None, None),
    ("bash", ": > important.db", A, None, None),
    ("bash", "npm run clean", A, None, None),
    ("bash", "bash cleanup.sh", A, None, None),
    # ---- PowerShell: every Remove-Item alias is unconditional
    ("pwsh", "rmdir d", P, None, "ri.any"),
    ("pwsh", "rd d", P, None, "ri.any"),
    ("pwsh", "del a.txt", P, None, "ri.any"),
    ("pwsh", "erase a.txt", P, None, "ri.any"),
    ("pwsh", "rm a.txt", P, None, "ri.any"),
    ("pwsh", "ri a.txt", P, None, "ri.any"),
    ("pwsh", "Remove-Item d -r", P, None, "ri.recurse"),
    ("pwsh", "Remove-Item d -rec -fo", P, None, "ri.recurse"),
    ("pwsh", "Remove-Item d -Recurse:$false", P, None, "ri.any"),
    ("pwsh", "Remove-Item d -Recurse -WhatIf", A, None, None),
    ("pwsh", "Remove-Item Env:FOO", A, None, None),
    ("pwsh", "dz safedel --yes d/* && rmdir d -r -fo", D, "R2", None),
    ("pwsh", "dz safedel --yes d && rmdir d", D, "R2", None),
    ("pwsh", "dz safedel --yes d && [IO.Directory]::Delete('d')", P, None, "safedel.stage"),
    ("pwsh", "dz safedel --yes d && cmd /c rd d", P, None, "safedel.stage"),
    ("pwsh", "Remove-Item a -Recurse -Force; Remove-Item b -Recurse -Force", D, "R2", "ri.recurse"),
    ("pwsh", "Get-ChildItem *.tmp | Remove-Item", D, "R3", "ri.any"),
    ("pwsh", "Get-ChildItem | ForEach-Object { Remove-Item $_ -Recurse }", D, "R3", "ri.recurse"),
    ("pwsh", "if (Test-Path x) { Remove-Item x -Force }", D, "R3", "ri.any"),
    ("pwsh", "Remove-Item x 2>$null", D, "R3", "ri.any"),
    ("pwsh", "[IO.Directory]::Delete('d')", A, None, None),
    ("pwsh", "[IO.Directory]::Delete('d', $true)", P, None, "dotnet.dir.delete.recursive"),
    ("pwsh", "$j = Get-Item x; $j.Delete()", D, "R3", "dotnet.obj.delete"),
    ("pwsh", '$dz = (Get-Command dz).Source; "dz = $dz"', A, None, None),
    ("pwsh", "git worktree remove w", D, "R1", "git.worktree.remove"),
    ("pwsh", "& $tool -Recurse; 'Remove-Item'", D, "R0", "guard.opaque"),
    ("pwsh", "Write-Output 'Remove-Item x -Recurse; rm'", A, None, None),
    # ---- cmd
    ("cmd", "rd /s /q d", P, None, "rmdir.cmd-recursive"),
    ("cmd", "rd d", A, None, None),
    ("cmd", "rd a & rd b", A, None, None),
    ("cmd", "del /q a.txt", P, None, "del.any"),
    ("cmd", "del a.txt & del b.txt", D, "R2", "del.any"),
    ("cmd", 'echo "rd /s /q d & del x"', A, None, None),
]


@pytest.mark.parametrize("shell,line,decision,rule,form", TABLE,
                         ids=["%s:%s" % (t[0], t[1][:48].replace("\n", " ")) for t in TABLE])
def test_table(shell, line, decision, rule, form):
    v = G.judge(line, shell)
    assert v.decision == decision, "%r -> %r (%s)" % (line, v, v.why)
    if rule is not None:
        assert v.rule == rule, "%r -> %r" % (line, v)
    if form is not None:
        got = v.culprit.id if (v.culprit is not None and v.decision in (D, K)) else (v.ids[0] if v.ids else None)
        assert got == form, "%r -> %r" % (line, v)


def test_incident_line_names_every_deletion():
    v = G.judge(INCIDENT, "bash")
    assert v.denied
    assert v.ids.count("git.worktree.remove") == 2 and v.ids.count("safedel.stage.noyes") == 2
    text = G.render(v)
    assert "dz safedel --yes <path>" in text
    assert text.splitlines()[-1].startswith("guard: BLOCKED R1 ")


def test_human_context_does_not_demand_yes():
    # the same file feeds safedel's line guard for a human at a console: no --yes is right there
    assert G.judge("dz safedel a", "bash", {"agent": False}).decision == P
    assert G.judge("dz safedel a; rm -rf b", "bash", {"agent": False}).rule == "R2"


FORMS = [(e, f) for e, f in G.iter_forms()]
FORM_IDS = [f["id"] for _e, f in FORMS]


@pytest.mark.parametrize("entry,form", FORMS, ids=FORM_IDS)
def test_self_example(entry, form):
    """Every example of a form must classify to exactly that form (no shadowing, no dead forms)."""
    assert form["examples"], "form has no examples"
    for shell, line in form["examples"]:
        v = G.judge(line, shell)
        ids = [v.culprit.id] if (v.culprit is not None and v.culprit.form.get("synthetic")) else v.ids
        assert ids == [form["id"]], "%s: %r classified as %r (%r)" % (form["id"], line, ids, v)


@pytest.mark.parametrize("entry,form", FORMS, ids=FORM_IDS)
def test_message_round_trip(entry, form):
    """Whatever a refusal tells the caller to run must itself pass the judge."""
    shells = sorted(set(sh for sh, _l in form["examples"]))
    for shell in shells:
        for command in G.use_lines(form, shell):
            v = G.judge(G.fill(command), shell)
            assert not v.denied, "%s recommends %r, which the judge refuses: %r %s" % (form["id"], command, v, v.why)
    # and through the rendered text, for every example that is actually refused
    for shell, line in form["examples"]:
        v = G.judge(line, shell)
        if v.decision not in (D, K):
            continue
        text = G.render(v)
        assert text.isascii()
        assert text.splitlines()[-1].startswith("guard: ")
        for command in G.recommended(text):
            if command.startswith("<"):
                continue
            w = G.judge(G.fill(command), shell)
            assert not w.denied, "message for %r recommends refused %r" % (line, command)


def test_every_refusal_in_the_table_recommends_something_allowed():
    for shell, line, decision, _rule, _form in TABLE:
        if decision != D:
            continue
        v = G.judge(line, shell)
        text = G.render(v)
        assert G.USE_HEADER in text, "refusal for %r teaches nothing" % line
        for command in G.recommended(text):
            if not command.startswith("<"):
                assert not G.judge(G.fill(command), shell).denied


def test_required_fields():
    assert G.validate_registry() == []


def test_required_fields_check_can_fail():
    """Red side of the test above: break the table and the check must notice (then restore)."""
    form = G._FORMS["rmdir.plain"]
    saved = form["evidence"]
    try:
        form["evidence"] = G.E("argued", "looks self-checking")
        assert any("MEASURED" in b for b in G.validate_registry())
    finally:
        form["evidence"] = saved
    form = G._FORMS["rm.recursive"]
    saved = form.pop("post")
    try:
        assert any("post-condition" in b for b in G.validate_registry())
    finally:
        form["post"] = saved
    assert G.validate_registry() == []


def test_table_is_data():
    """`when` is nested tuples of names and constants: no lambdas, no code in the table."""
    def walk(x):
        assert isinstance(x, (tuple, str, int)), "non-data in a `when`: %r" % (x,)
        if isinstance(x, tuple):
            for y in x:
                walk(y)
    for _e, f in FORMS:
        walk(f["when"])
        assert f["class"] in G.CLASSES


def test_judge_module_cannot_run_anything():
    """The module imports nothing that can start a process or remove a tree."""
    lines = [ln.strip() for ln in io.open(GUARD_PATH, encoding="utf-8").read().splitlines()]
    imports = [ln for ln in lines if ln.startswith(("import ", "from "))]
    assert sorted(imports) == ["from __future__ import annotations", "import base64", "import json",
                               "import os", "import re", "import sys", "import time"]
    assert not hasattr(G, "subprocess") and not hasattr(G, "shutil")
    # behaviour is covered by the autouse fixture: every table line is judged with os.system,
    # os.remove, os.unlink, os.rmdir, shutil.rmtree and subprocess patched to raise.


def test_ps_param_prefixes():
    for tok in ("-r", "-re", "-rec", "-Recurse", "-RECURSE:$true"):
        assert G.ps_param(tok, "recurse")
    for tok in ("-Recurse:$false", "-recurse:0", "-x", "recurse", "-recursed"):
        assert not G.ps_param(tok, "recurse")
    assert G.ps_param("-fo", "force", 2) and not G.ps_param("-f", "force", 2)


def test_exit_codes_and_verdict_line():
    assert sorted(G.EXIT_CODES) == [0, 1, 2, 3, 4, 5, 6]
    assert (G.EXIT_REFUSED, G.EXIT_BLOCKED, G.EXIT_POSTCONDITION) == (3, 5, 6)
    assert G.verdict_line("safedel", "STAGED", "2") == "safedel: STAGED 2"
    assert G.verdict_line("safedel", "REFUSED") == "safedel: REFUSED"


# ---- hook mode -------------------------------------------------------------

def run_hook(payload, env=None, raw=None):
    out, err = io.StringIO(), io.StringIO()
    # These hook tests exercise the whole table; the hook's default scope ("safedel") is tested
    # on its own further down.
    e = {"DZ_GUARD_LOG": "off", "DZ_GUARD_SCOPE": "full"}
    e.update(env or {})
    text = raw if raw is not None else json.dumps(payload)
    code = G.main([], stdin=io.StringIO(text), stdout=out, stderr=err, env=e)
    return code, out.getvalue(), err.getvalue()


def test_hook_blocks_incident_with_exit_2():
    code, out, err = run_hook({"tool_name": "Bash", "tool_input": {"command": INCIDENT}})
    assert code == 2 and out == ""
    assert err.rstrip().splitlines()[-1].startswith("guard: BLOCKED")


def test_hook_allows_and_ignores_other_tools():
    assert run_hook({"tool_name": "Bash", "tool_input": {"command": "ls -la | head"}})[0] == 0
    assert run_hook({"tool_name": "Bash", "tool_input": {"command": "dz safedel --yes a && git worktree prune"}})[0] == 0
    assert run_hook({"tool_name": "Write", "tool_input": {"file_path": "x", "content": "rm -rf /"}})[0] == 0
    assert run_hook({"tool_name": "PowerShell", "tool_input": {"command": "Get-ChildItem | Remove-Item -Recurse"}})[0] == 2


def test_hook_fails_closed_only_when_a_deletion_is_in_sight():
    assert run_hook(None, raw='{"tool_name": "Bash", "tool_input": {"command": "rm -rf x"')[0] == 2
    assert run_hook(None, raw="not json at all")[0] == 0


def test_hook_ask_and_unattended():
    p = {"tool_name": "Bash", "tool_input": {"command": "dz guarded --yes dangerous -- git worktree remove w"}}
    code, out, _err = run_hook(p)
    assert code == 0 and json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "ask"
    code, out, err = run_hook(p, env={"DZ_GUARD_UNATTENDED": "1"})
    assert code == 2 and out == "" and "guard: BLOCKED" in err


def test_hook_logs_blocks(tmp_path):
    log = tmp_path / "guard.jsonl"
    run_hook({"tool_name": "Bash", "tool_input": {"command": "rm -rf a && rm -rf b"}}, env={"DZ_GUARD_LOG": str(log)})
    run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}, env={"DZ_GUARD_LOG": str(log)})
    rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1 and rows[0]["decision"] == "DENY" and rows[0]["rule"] == "R2"


def test_explain_mode_uses_graded_exit_code():
    out = io.StringIO()
    full = {"DZ_GUARD_SCOPE": "full"}
    assert G.main(["--judge", "rm -rf a; ls"], stdout=out, stderr=io.StringIO(), env=full) == G.EXIT_BLOCKED
    assert G.main(["--judge", "ls"], stdout=io.StringIO(), stderr=io.StringIO(), env=full) == 0
    # default scope: the same line has no dz safedel step, so it is outside the guard's scope
    assert G.main(["--judge", "rm -rf a; ls"], stdout=io.StringIO(), stderr=io.StringIO(), env={}) == 0
    assert G.main(["--check"], stdout=io.StringIO(), stderr=io.StringIO(), env={}) == 0


# ---- scope: the hook's default is "safedel" (the maintainer, 2026-11-19) -----------------------

def run_hook_default_scope(command, shell_tool="Bash"):
    """The hook as it will be installed: no DZ_GUARD_SCOPE in the environment."""
    out, err = io.StringIO(), io.StringIO()
    text = json.dumps({"tool_name": shell_tool, "tool_input": {"command": command}})
    code = G.main([], stdin=io.StringIO(text), stdout=out, stderr=err, env={"DZ_GUARD_LOG": "off"})
    return code, err.getvalue()


@pytest.mark.parametrize("command", [
    "rm -rf a && rm -rf b",                       # no dz safedel on the line: not policed
    "rm -rf /t",
    "git status && git checkout -- src/a.py",
    "git commit --amend --no-edit && git push --force-with-lease",
    'python -c "import shutil; shutil.rmtree(\'build\')"',
    "dz safedel list && rm -rf build",            # list/status is not a destructive safedel step
])
def test_default_scope_leaves_lines_without_a_safedel_step_alone(command):
    assert run_hook_default_scope(command)[0] == 0


@pytest.mark.parametrize("command", [
    "dz safedel --yes a && rm -rf b",             # the pattern the guard exists for
    "dz safedel --yes a 2>&1 | tail -1",
    "dz safedel --yes a; git worktree remove w",
    "dz safedel a",                               # no --yes
    "dz safedel --yes a > /dev/null 2>&1",
    "git worktree remove w",                      # the incident's own form, always
    "git clean -fdx",
])
def test_default_scope_blocks_the_safedel_and_incident_patterns(command):
    code, err = run_hook_default_scope(command)
    assert code == 2, err
    assert "guard: BLOCKED" in err


def test_default_scope_allows_the_safe_sequence():
    assert run_hook_default_scope("dz safedel --yes a && git worktree prune")[0] == 0
    assert run_hook_default_scope("cd /tmp/x && dz safedel --yes build")[0] == 0


@pytest.mark.parametrize("command", [
    "$DZ safedel --yes a",                        # command name from an expansion
    'echo "dz safedel --yes a" | bash',           # text piped into a shell
    "$(echo dz) safedel --yes a",
])
def test_default_scope_still_fails_closed_on_an_obscured_safedel_line(command):
    """A line the guard cannot name or read stays in scope when it mentions safedel, so hiding
    the command name is not a way around the guard."""
    code, err = run_hook_default_scope(command)
    assert code == 2, err
    assert G.judge(command, "bash", {"scope": "safedel"}).rule == "R0"


def test_an_unknown_scope_value_falls_back_to_safedel_not_full_or_off():
    assert G._scope_from_env({"DZ_GUARD_SCOPE": "everything"}) == "safedel"
    assert G._scope_from_env({"DZ_GUARD_SCOPE": " FULL "}) == "full"
    assert G._scope_from_env({}) == "safedel"


def test_judge_itself_still_defaults_to_the_whole_table():
    assert G.judge("rm -rf a && rm -rf b", "bash").decision == D
    assert G.judge("rm -rf a && rm -rf b", "bash", {"scope": "safedel"}).decision == A


# ---- the shared file: byte-identical copies --------------------------------

def sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def test_sha256_equality_mechanism(tmp_path):
    """The check that will pin the lib copy to the installed hook copy; shown able to fail."""
    same = tmp_path / "_guard_copy.py"
    same.write_bytes(open(GUARD_PATH, "rb").read())
    assert sha256(str(same)) == sha256(GUARD_PATH)
    drift = tmp_path / "_guard_drift.py"
    drift.write_bytes(open(GUARD_PATH, "rb").read() + b"\n# drift\n")
    assert sha256(str(drift)) != sha256(GUARD_PATH)


def test_sha256_of_declared_copies():
    """DZ_GUARD_COPIES = os.pathsep-separated paths of every other copy (lib module, installed hook)."""
    copies = [p for p in os.environ.get("DZ_GUARD_COPIES", "").split(os.pathsep) if p]
    if not copies:
        pytest.skip("no other copies declared (prototype stage: one file)")
    for p in copies:
        assert sha256(p) == sha256(GUARD_PATH), "copy drifted: %s" % p


# ---------------------------------------------------------------------------------------------
# D1-D5, the maintainer's decisions (2026-11-19: "all 5 recommendation", to be revisited after
# dogfooding). One test per decision, pinning the behaviour the recommendation chose.

@pytest.mark.parametrize("line, decision", [
    ("dz safedel build", D),                 # D1: a bare dz safedel is refused (second net; safedel
    ("dz safedel --yes build", P),           #     itself refuses without --yes since 0.10.37)
])
def test_d1_bare_safedel_is_refused(line, decision):
    assert G.judge(line, "bash").decision == decision


@pytest.mark.parametrize("line", [
    'python -c "import shutil; shutil.rmtree(\'build\')"',
    "python - <<'PY'\nimport os\nos.remove('a.db')\nPY",
])
def test_d2_interpreter_deletes_are_always_refused(line):
    assert G.judge(line, "bash").decision == D


def test_d3_no_scratch_exemption_in_v1():
    assert G.judge("rm -rf /tmp/scratch-1/out", "bash").decision == P     # alone: allowed, checked
    assert G.judge("rm -rf /tmp/a && rm -rf /tmp/b", "bash").decision == D  # chained: refused


@pytest.mark.parametrize("line", ["rm -rf build 2>/dev/null", "rm -rf build > /dev/null 2>&1",
                                  "rm -rf node_modules &", "dz safedel --yes build > /dev/null 2>&1"])
def test_d4_discarded_or_backgrounded_deletes_are_refused(line):
    assert G.judge(line, "bash").decision == D


@pytest.mark.parametrize("shell, line, decision", [
    ("bash", "git status && git checkout -- src/a.py", D),     # D5: unconditional in a chain
    ("bash", "git status && git restore src/a.py", D),
    ("pwsh", "Get-ChildItem x; Remove-Item -Recurse x", D),
    ("bash", "git commit --amend --no-edit && git push --force-with-lease", D),
    ("bash", "git commit --amend --no-edit && git push --force-with-lease --force-if-includes", A),
    ("bash", "git push --force-with-lease --force-if-includes --force", P),   # an extra --force undoes it
    ("bash", "git push --force-with-lease --force-if-includes origin +main", P),
])
def test_d5_lease_with_includes_is_self_checking_the_rest_unconditional(shell, line, decision):
    assert G.judge(line, shell).decision == decision


@pytest.mark.parametrize("line", ["x=$(rm -rf build)", "$(rm -rf build)",
                                  "out=$(dz safedel --yes build)"])
def test_a_deletion_inside_a_command_substitution_is_refused_for_that_reason(line):
    """Pinned by the G3 audit, which found this branch unguarded: with nothing else on the line
    the chain rules do not fire, so only the substitution check stands between these and an allow."""
    v = G.judge(line, "bash")
    assert v.decision == D
    assert "command substitution" in v.why


@pytest.mark.parametrize("line", ["cd /tmp/x && ls && rm -rf build",
                                  "cd /tmp/x && git status && rm -rf build"])
def test_one_cd_does_not_excuse_another_command_before_an_unconditional_delete(line):
    """Killed mutant M7 of the G3 sweep (all -> any): only 'cd <literal> &&' may precede an
    unconditional deletion, and every other command on the line must be such a cd."""
    v = G.judge(line, "bash")
    assert (v.decision, v.rule) == (D, "R3")


@pytest.mark.parametrize("line", ["test -d build || dz safedel --yes build",
                                  "dz safedel --yes build || echo failed"])
def test_a_staging_step_joined_by_or_is_refused(line):
    """Killed mutant M9 of the G3 sweep: '||' runs one side only when the other fails, which hides
    the staging step's result; only '&&' may join it."""
    v = G.judge(line, "bash")
    assert (v.decision, v.rule) == (D, "R4")
    assert G.judge("cd /tmp/x && dz safedel --yes build", "bash").decision == P


def test_guard_imports_only_the_standard_library():
    """The hook runs `python -I _guard.py`; a third-party or package import would fail there,
    and importing the library would cost about 0.4 s per tool call."""
    import ast
    with open(GUARD_PATH, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    stdlib = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
    assert names and names <= stdlib, sorted(names - stdlib)


def test_exit_codes_agree_with_safedel():
    """Two definitions on purpose (the hook cannot import the package); they must not drift."""
    from dazzlecmd_lib.core.safedel import _verdict
    for name in ("EXIT_OK", "EXIT_ERROR", "EXIT_USAGE", "EXIT_REFUSED", "EXIT_PARTIAL",
                 "EXIT_BLOCKED", "EXIT_POSTCONDITION"):
        assert getattr(G, name) == getattr(_verdict, name), name
    assert G.EXIT_CODES == _verdict.EXIT_CODES
