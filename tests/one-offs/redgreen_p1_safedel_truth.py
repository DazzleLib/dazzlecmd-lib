"""Red-green audit for P1 (safedel tells the truth), run on a COPY.

A test written after the code was born green and has never shown it can fail.
This script reverts one behaviour at a time and records which of the new
tests go red. A behaviour whose reversion turns nothing red is not pinned.

Safety:
- The real source is never modified. The engine (dazzlecmd_lib/), the CLI
  (safedel.py + its _lib/) and the new tests are copied into a fresh temp
  folder; every mutation is applied to the copy, and pytest runs against the
  copy only (PYTHONPATH and cwd point at it). An interrupted run leaves the
  real tree exactly as it was.
- The tests themselves are rule-1b safe under mutation: stores live under
  tmp_path with an explicit registry, and every test asserting a refusal
  replaces the functions that move, delete, copy or create with ones that
  raise. That is what makes it safe to switch guards off here.
- Nothing is deleted by this script; the temp folder is left in place and
  its path printed.

Usage:  python tests/one-offs/redgreen_p1_safedel_truth.py [name-substring]
Writes the report to stdout; exit 1 if any behaviour is unpinned (apart from
those marked as covered by a second layer, which are audited by a paired
mutation).
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LIB_REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
TOOL_DIR = os.environ.get(
    "SAFEDEL_TOOL_DIR",
    r"C:\code\dazzlecmd\github\src\dazzlecmd\projects\core\safedel")

SD = "lib/dazzlecmd_lib/core/safedel/"
CLI = "tool/safedel.py"
WT = "wt/worktree_teardown.py"
WT_DIR = os.environ.get(
    "WORKTREE_TEARDOWN_DIR",
    r"C:\code\dazzlecmd\github\src\dazzlecmd\projects\dazzletools\worktree-teardown")

# (name, [(file, old, new), ...], note)
MUTATIONS = [
    ("console: trust isatty on Windows again (the 2026-11-17 bug)",
     [(SD + "_console.py", 'if platform != "win32":', "if True:")], ""),
    ("console: MSYS pty pipe not recognised",
     [(SD + "_console.py", "return is_msys_pty_name(pipe_name(handle))", "return False")], ""),
    ("scopes: any word after --yes is read as scopes (swallows a path)",
     [(SD + "_scopes.py", "    if all(known):\n        return \"all\"", "    if True:\n        return \"all\"")], ""),
    ("scopes: a mistyped scope list is treated as a path",
     [(SD + "_scopes.py", '        return "some"', '        return "none"')], ""),
    ("verdict: a refusal is not reported first",
     [(SD + "_verdict.py", 'if getattr(result, "refused", False):', "if False:")], ""),
    ("verdict: a failed post-condition is ignored",
     [(SD + "_verdict.py", "    if post:\n", "    if False:\n")], ""),
    ("verdict: errors with clean counts read as STAGED",
     [(SD + "_verdict.py", "if staged and not not_staged and not errors:", "if staged and not not_staged:")], ""),
    ("verdict: the line is not flattened to one line",
     [(SD + "_verdict.py", 'detail = " ".join(str(detail).split())', "detail = str(detail)")], ""),
    ("plan: same-name batch is not refused",
     [(SD + "_store.py", "        if clashes:\n", "        if False:\n")], ""),
    ("plan: names compared case-sensitively",
     [(SD + "_store.py", 'return unicodedata.normalize("NFC", name).casefold()', 'return unicodedata.normalize("NFC", name)')], ""),
    ("plan: a known link's destination is not refused (the incident)",
     [(SD + "_store.py", "hits = links_pointing_at(item.path, self.junction_registry_path)", "hits = []")], ""),
    ("junctions: only the nearest enclosing repo is read",
     [(SD + "_junctions.py", "            repos.append(current)\n", "            return [current]\n")], ""),
    ("junctions: a relative gitdir is dropped",
     [(SD + "_junctions.py", "        if not os.path.isabs(gitdir):\n", "        if False:\n")], ""),
    ("plan: wrong delete-through scope for the link type is accepted",
     [(SD + "_store.py", "            if is_j and want_s and not want_j:\n", "            if False:\n"),
      (SD + "_store.py", "            if is_s and want_j and not want_s:\n", "            if False:\n")], ""),
    ("plan: a delete-through scope that matches nothing is accepted",
     [(SD + "_store.py", "        if want_j and not used_j and not plan.refusals:\n", "        if False:\n")], ""),
    ("plan: delete-through scope does not stage the target",
     [(SD + "_store.py", "            if (is_j and want_j) or (is_s and want_s):\n", "            if False:\n")], ""),
    ("plan: all paths missing is not a refusal",
     [(SD + "_store.py", "        if not existing:\n            plan.refusals.append(", "        if False:\n            plan.refusals.append(")], ""),
    ("plan: a store path that is not absolute is accepted",
     [(SD + "_store.py", "        if not is_usable_store_root(plan.store_path):\n            plan.refusals.append(", "        if False:\n            plan.refusals.append(")], ""),
    ("copy-first: the size limit is not enforced",
     [(SD + "_store.py", "            if plan.copy_first_bytes > plan.copy_first_max:\n", "            if False:\n")], ""),
    ("copy-first: the limit refuses a batch exactly at it",
     [(SD + "_store.py", "plan.copy_first_bytes > plan.copy_first_max", "plan.copy_first_bytes >= plan.copy_first_max")], ""),
    ("copy-first: a file copy is not verified",
     [(SD + "_copyfirst.py", "            if _sha256(path) != _sha256(dest):\n", "            if False:\n")], ""),
    ("copy-first: the tree copy enters links",
     [(SD + "_copyfirst.py", "            if _is_link(st):\n                try:\n                    target = os.readlink(s_path)", "            if False:\n                try:\n                    target = os.readlink(s_path)")], ""),
    ("copy-first: no copy is made at all",
     [(SD + "_store.py", "            if item.copy_first and item.staged_name:\n", "            if False:\n")], ""),
    ("survey: links are entered when counting",
     [(SD + "_copyfirst.py", '            if _is_link(st):\n                out["links"] += 1', '            if False:\n                out["links"] += 1')], ""),
    ("execute: missing paths are not counted as not staged",
     [(SD + "_store.py", '            errors.append(f"Skipping non-existent path: {path}")\n            not_staged += 1', '            errors.append(f"Skipping non-existent path: {path}")')], ""),
    ("execute: the manifest never says a run is unfinished",
     [(SD + "_store.py", '            manifest["state"] = state\n', '            manifest["state"] = "complete"\n')], ""),
    ("post-condition: an original that still exists is not noticed",
     [(SD + "_store.py", "            if os.path.lexists(item.path):\n                failures.append(", "            if False:\n                failures.append(")], ""),
    ("post-condition: a staged item missing from the trash is not noticed",
     [(SD + "_store.py", "                if not os.path.lexists(where):\n", "                if False:\n")], ""),
    ("post-condition: a changed link target is not noticed",
     [(SD + "_store.py", "                if now != item.link_target_entries:\n", "                if False:\n")], ""),
    ("timepattern: the batch folder is looked up, not claimed",
     [(SD + "_timepattern.py", "            os.mkdir(os.path.join(trash_dir, candidate))\n            return candidate", "            os.makedirs(os.path.join(trash_dir, candidate), exist_ok=True)\n            return candidate")], ""),
    ("volumes (Windows): a store is built on a name without a drive",
     [(SD + "_volumes.py", "    if not os.path.splitdrive(drive)[0]:\n", "    if False:\n")], "windows"),
    ("volumes (POSIX): a store is built on a drive-letter name",
     [(SD + "_volumes.py", "    if not is_usable_store_root(volume.mount_point):\n        return None\n    uid = os.getuid()", "    uid = os.getuid()")], "posix"),
    ("volumes: a relative store root counts as usable",
     [(SD + "_volumes.py", "    if not path or not os.path.isabs(path):\n        return False", "    if not path:\n        return False")], ""),
    ("recover: 'last' picks an unfinished or unreadable batch",
     [(SD + "_recover.py", '            if folder.state == "complete":\n', "            if True:\n")], ""),
    ("recover: an unreadable manifest is 'zero entries, all done' (layer 1)",
     [(SD + "_recover.py", "    if folder.manifest_error:\n        # No manifest means", "    if False:\n        # No manifest means")], ""),
    ("recover: an unreadable manifest does not block removal (layer 2 alone)",
     [(SD + "_recover.py", '    if folder.manifest_error:\n        return f"its manifest is {folder.manifest_error}"\n    if not folder.entries:', '    if False:\n        return f"its manifest is {folder.manifest_error}"\n    if not folder.entries:')],
     "second-layer"),
    ("recover: both manifest layers off (the data-loss path)",
     [(SD + "_recover.py", "    if folder.manifest_error:\n        # No manifest means", "    if False:\n        # No manifest means"),
      (SD + "_recover.py", '    if folder.manifest_error:\n        return f"its manifest is {folder.manifest_error}"\n    if not folder.entries:\n        return "its manifest lists no entries"', '    if False:\n        return f"its manifest is {folder.manifest_error}"\n    if False:\n        return "its manifest lists no entries"')], ""),
    ("recover: a folder that still holds content is removed",
     [(SD + "_recover.py", "    if left:\n        return (f", "    if False:\n        return (f")], ""),
    ("recover: the folder is removed by name, not by path",
     [(SD + "_recover.py", "store.remove_folder(folder.folder_name, folder.folder_path)", "store.remove_folder(folder.folder_name)")], ""),
    ("store: remove_folder accepts a path that is not that folder",
     [(SD + "_store.py", '            if os.path.basename(folder_path.rstrip("\\\\/")) != folder_name:\n', "            if False:\n")], ""),
    ("store: an unreadable manifest loads as an empty batch",
     [(SD + "_store.py", '                manifest_error = f"unreadable ({exc})"', "                manifest_error = None")], ""),
    ("store: a missing manifest is not reported",
     [(SD + "_store.py", '            manifest_error = "missing"', "            manifest_error = None")], ""),
    ("list: only the central store is named",
     [(SD + "_recover.py", "    stores = sorted({os.path.dirname(f.folder_path) for f in folders})", "    stores = [store.store_path]")], ""),
    ("status: only the central store is named",
     [(SD + "_recover.py", "        key = os.path.dirname(f.folder_path)\n", "        key = stats.store_path\n")], ""),
    # -- P3: worktree teardown and the junction registry --
    ("worktree: a main checkout is not told apart",
     [(SD + "_worktree.py", "    if stat.S_ISDIR(st.st_mode) and not _is_reparse(st):\n        return None, None, (\"its .git is a folder", "    if False:\n        return None, None, (\"its .git is a folder")], ""),
    ("worktree: the links are not removed before the folder moves (layer 1)",
     [(SD + "_worktree.py", "    for link in plan.links:\n        r = store.trash([link.path], scopes=scopes)", "    for link in []:\n        r = store.trash([link.path], scopes=scopes)")], ""),
    ("worktree: links left inside do not stop the move (layer 2 alone)",
     [(SD + "_worktree.py", "    if left:\n        result.errors.append(\n            \"links are still inside", "    if False:\n        result.errors.append(\n            \"links are still inside")], "second-layer"),
    ("worktree: both link layers off (the folder moves with its links)",
     [(SD + "_worktree.py", "    for link in plan.links:\n        r = store.trash([link.path], scopes=scopes)", "    for link in []:\n        r = store.trash([link.path], scopes=scopes)"),
      (SD + "_worktree.py", "    if left:\n        result.errors.append(\n            \"links are still inside", "    if False:\n        result.errors.append(\n            \"links are still inside")], ""),
    ("worktree: a changed link target is not noticed",
     [(SD + "_worktree.py", "                if now != link.target_entries:\n                    result.errors.append(\n                        f\"{after}: the target", "                if False:\n                    result.errors.append(\n                        f\"{after}: the target")], ""),
    ("worktree: a failed prune is ignored",
     [(SD + "_worktree.py", "        if rc not in (0, None):\n", "        if False:\n")], ""),
    ("worktree: the search for links enters links",
     [(SD + "_worktree.py", "            if _is_reparse(st):\n                try:\n                    info = detect_link(entry.path)", "            if False:\n                try:\n                    info = detect_link(entry.path)")], ""),
    ("worktree: links are removed shallowest first",
     [(SD + "_worktree.py", "key=lambda l: l.path.count(os.sep), reverse=True)", "key=lambda l: l.path.count(os.sep), reverse=False)")], ""),
    ("worktree: removed links stay in the registry",
     [(SD + "_worktree.py", "                unregister_link(link.path, registry_path)", "                pass")], ""),
    ("registry: registering a link twice duplicates it",
     [(SD + "_junctions.py", "    records = [r for r in read_registry(registry_path)\n               if _key(r[\"link\"]) != _key(link_abs)]\n    records.append(record)", "    records = list(read_registry(registry_path))\n    records.append(record)")], ""),
    # -- P3: the dz worktree-teardown command line --
    ("wt cli: no --yes and nobody at the keyboard proceeds",
     [(WT, "        if not stdin_is_interactive():\n", "        if False:\n")], ""),
    ("wt cli: a declined prompt proceeds",
     [(WT, '        if _ask("  Proceed? [y/N]: ") != "y":\n', "        if False:\n")], ""),
    ("wt cli: a dry run executes",
     [(WT, "    if args.dry_run:\n", "    if False:\n")], ""),
    ("wt cli: a refused plan is executed",
     [(WT, "    if plan.refused:\n        print(\"\\n  worktree-teardown: REFUSED", "    if False:\n        print(\"\\n  worktree-teardown: REFUSED")], ""),
    ("wt cli: a teardown stopped part-way exits 0",
     [(WT, '        return _finish(EXIT_PARTIAL, "STOPPED",', '        return _finish(EXIT_OK, "STOPPED",')], ""),
    ("wt cli: the verdict line is not printed",
     [(WT, '    print(verdict_line("worktree-teardown", outcome, detail))\n', "")], ""),
    ("wt cli: an older library crashes instead of reporting",
     [(WT, "    if _IMPORT_ERROR is not None:\n        print(\"  worktree-teardown needs", "    if False:\n        print(\"  worktree-teardown needs")], ""),
    ("wt cli: a scope error is not a usage error",
     [(WT, '        return _finish(EXIT_USAGE, "USAGE", str(exc))', '        argv, scopes, yes_given = [a for a in argv if "," not in a and not a.startswith("--yes")], (), True')], ""),
    ("clean: a refusal exits 0",
     [(SD + "_recover.py", "    return EXIT_REFUSED\n", "    return EXIT_OK\n")], ""),
    ("mode: --force overrides a refusal",
     [("lib/dazzlecmd_lib/mode.py", "    if refused:\n        # A refusal is safedel saying", "    if False:\n        # A refusal is safedel saying")], ""),
    # -- 0.10.39: the common-base layout, and what recovery owes it --
    ("layout: items keep their bare names (the layout step is skipped)",
     [(SD + "_store.py", "        self._plan_layout(plan)\n", "        pass\n")], ""),
    ("layout: what the planner cannot place is not a refusal",
     [(SD + "_store.py", '            plan.refusals.append(f"the batch cannot be laid out: {message}")', "            pass")], ""),
    ("layout: a missing planner crashes instead of refusing",
     [(SD + "_store.py", "        except ImportError:\n            plan.refusals.append(\n                \"this version of safedel", "        except KeyError:\n            plan.refusals.append(\n                \"this version of safedel")], ""),
    ("layout: an item the planner dropped stays in the plan",
     [(SD + "_store.py", "            if relpath is None:\n                if layout.errors:\n", "            if relpath is None:\n                if True:\n")], ""),
    ("layout: links that take no place still stretch the base",
     [(SD + "_store.py", "        placed_items = [i for i in plan.items if i.staged_name is not None]", "        placed_items = list(plan.items)")], ""),
    ("layout: an item inside a named link is not refused",
     [(SD + "_store.py", "                        and _inside(other.path, link.path):", "                        and False:")], ""),
    ("layout: the multi-root warning is not given",
     [(SD + "_store.py", "        if len(layout.bases) > 1:\n", "        if False:\n")], ""),
    ("layout: the manifest does not say which layout it uses",
     [(SD + "_store.py", '            manifest["content_layout"] = CONTENT_LAYOUT\n', "")], ""),
    ("layout: the manifest does not record the bases",
     [(SD + "_store.py", '            manifest["bases"] = list(plan.bases)\n', "")], ""),
    ("layout: content_path records the bare name, not the place",
     [(SD + "_store.py", '                entry.content_path = "content/" + item.staged_name\n', '                entry.content_path = "content/" + os.path.basename(c.path)\n')], ""),
    ("layout: a staged file's size is not recorded",
     [(SD + "_store.py", "                if not c.is_dir and c.file_type not in _LINK_TYPES:\n                    try:\n                        entry.staged_size", "                if False:\n                    try:\n                        entry.staged_size")], ""),
    ("layout: the item is staged under its bare name, not its place",
     [(SD + "_store.py", "                sr = _stage_item(c.path, under(content_dir, item.staged_name), c)", "                sr = stage_to_trash(c.path, content_dir, c)")], ""),
    ("recover: --to ignores an item's place in the batch",
     [(SD + "_recover.py", "        if relpath:\n            target = os.path.join(to_path, *relpath)", "        if False:\n            target = os.path.join(to_path, *relpath)")], ""),
    ("recover: a dry run with --to creates the folder",
     [(SD + "_recover.py", "        if not dry_run:\n            os.makedirs(to_path, exist_ok=True)", "        if True:\n            os.makedirs(to_path, exist_ok=True)")], ""),
    ("recover: a stored file of the wrong size is recovered",
     [(SD + "_recover.py", "        if mismatch and not force_identity:\n", "        if False:\n")], ""),
    ("recover: force_identity is ignored",
     [(SD + "_recover.py", "        if mismatch and not force_identity:\n", "        if mismatch:\n")], ""),
    ("recover: an older batch's size record is not used",
     [(SD + "_recover.py", "    if expected is None and isinstance(entry.stat, dict):\n", "    if False:\n")], ""),
    ("recover: a changed modification time is not noted",
     [(SD + "_recover.py", "        if note:\n            print(f\"  NOTE: {entry.original_path}: {note}.\"", "        if False:\n            print(f\"  NOTE: {entry.original_path}: {note}.\"")], ""),
    ("recover: every file gets the modification-time note",
     [(SD + "_recover.py", "    if abs(float(recorded) - actual) <= 2.0:\n        return None", "    if False:\n        return None")], ""),
    ("recover: entries that share one stored item are all recovered",
     [(SD + "_recover.py", "        if id(entry) in lost:\n", "        if False:\n")], ""),
    ("recover: the shared item goes to the FIRST entry",
     [(SD + "_recover.py", "        owner = (matching or members)[-1]", "        owner = (matching or members)[0]")], ""),
    ("recover: the shared item's type is not matched to the entry",
     [(SD + "_recover.py", "        matching = [e for e in members if bool(e.is_dir) == on_disk_is_dir]", "        matching = list(members)")], ""),
    ("cli: no --yes and nobody at the keyboard proceeds",
     [(CLI, "        if not interactive or json_output:\n", "        if False:\n")], ""),
    ("cli: --json stands in for --yes at a console",
     [(CLI, "        if not interactive or json_output:\n", "        if not interactive:\n")], ""),
    ("cli: a declined prompt proceeds",
     [(CLI, '        if answer != "y":\n', "        if False:\n")], ""),
    ("cli: a refused plan is executed",
     [(CLI, "    if plan.refused:\n        sys.stdout.flush()    # the list above first, then the reasons", "    if False:\n        sys.stdout.flush()    # the list above first, then the reasons")], ""),
    ("cli: a dry run executes",
     [(CLI, "    if dry_run:\n        if not json_output:\n            print(\"  DRY RUN: nothing was changed.\")", "    if False:\n        if not json_output:\n            print(\"  DRY RUN: nothing was changed.\")")], ""),
    ("cli: the verdict line is not printed",
     [(CLI, "        sys.stderr.flush()\n        print(line)", "        sys.stderr.flush()")], ""),
    ("cli: every exit code is 0",
     [(CLI, "    sys.stdout.flush()\n    return code", "    sys.stdout.flush()\n    return 0")], ""),
    ("cli: copy-first is on at a console in auto mode",
     [(CLI, '                          or (mode == "auto" and not interactive))', '                          or (mode == "auto"))')], ""),
    ("cli: copy-first is never on in auto mode",
     [(CLI, '                          or (mode == "auto" and not interactive))', "                          )")], ""),
    ("cli: --yes copy-first does not force it on",
     [(CLI, '    elif "copy-first" in scopes:\n        use_copy_first = True', '    elif "copy-first" in scopes:\n        use_copy_first = False')], ""),
    ("cli: --yes no-copy-first does not force it off",
     [(CLI, '    if "no-copy-first" in scopes:\n        use_copy_first = False', '    if "no-copy-first" in scopes:\n        use_copy_first = True')], ""),
    ("cli: --copy-first-max is ignored",
     [(CLI, "            max_bytes = parse_size(args.copy_first_max)", "            parse_size(args.copy_first_max)")], ""),
    ("cli: a scope error is not a usage error",
     [(CLI, '            print(verdict_line("safedel", "USAGE", str(exc)))\n            return EXIT_USAGE', '            print(verdict_line("safedel", "USAGE", str(exc)))\n            argv, scopes, yes_given = [a for a in argv if not a.startswith("--yes") and "," not in a], ("noninteractive",), True')], ""),
    ("cli: an unexpected exception exits 0",
     [(CLI, '            return _finish(EXIT_ERROR, "ERROR",', '            return _finish(EXIT_OK, "ERROR",')], ""),
    ("cli: an older engine is not reported (the command goes on)",
     [(CLI, "    too_old = _engine_too_old()\n    if too_old:\n", "    too_old = _engine_too_old()\n    if False:\n")], ""),
    ("cli: a current engine takes the older-engine path",
     [(CLI, "    if _ENGINE_IMPORT_ERROR is None:\n        return None\n", "    if False:\n        return None\n")], ""),
    ("cli: --force-identity is accepted but not passed on",
     [(CLI, '        extra = {"force_identity": True} if args.force_identity else {}', "        extra = {}")], ""),
    ("cli: the report says 'recover last'",
     [(CLI, 'print(f"\\n  To recover:  dz safedel recover {result.folder_name}")', 'print(f"\\n  To recover:  dz safedel recover last")')], ""),
]


def build_copy(root):
    lib = os.path.join(root, "lib")
    tool = os.path.join(root, "tool")
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(os.path.join(LIB_REPO, "dazzlecmd_lib"),
                    os.path.join(lib, "dazzlecmd_lib"), ignore=ignore)
    os.makedirs(os.path.join(lib, "tests"))
    for name in ("conftest.py", "test_core_safedel_truth.py",
                 "test_core_safedel_worktree.py",
                 "test_core_safedel_layout.py"):
        shutil.copy2(os.path.join(LIB_REPO, "tests", name), os.path.join(lib, "tests", name))
    os.makedirs(os.path.join(tool, "tests"))
    shutil.copy2(os.path.join(TOOL_DIR, "safedel.py"), os.path.join(tool, "safedel.py"))
    shutil.copytree(os.path.join(TOOL_DIR, "_lib"), os.path.join(tool, "_lib"), ignore=ignore)
    for name in ("conftest.py", "test_cli_truth.py", "test_cli_report.py",
                 "test_cli_layout.py"):
        shutil.copy2(os.path.join(TOOL_DIR, "tests", name), os.path.join(tool, "tests", name))
    wt = os.path.join(root, "wt")
    os.makedirs(os.path.join(wt, "tests"))
    shutil.copy2(os.path.join(WT_DIR, "worktree_teardown.py"), os.path.join(wt, "worktree_teardown.py"))
    shutil.copy2(os.path.join(WT_DIR, "tests", "test_worktree_teardown.py"),
                 os.path.join(wt, "tests", "test_worktree_teardown.py"))
    return lib, tool


def run_tests(root):
    """Run both test sets against the copy; return (failed test ids, tail)."""
    env = dict(os.environ)
    # The copy goes FIRST; anything already on PYTHONPATH (the sibling
    # source trees a WSL run imports from) stays reachable after it.
    env["PYTHONPATH"] = os.pathsep.join(
        [os.path.join(root, "lib")]
        + [p for p in env.get("PYTHONPATH", "").split(os.pathsep)
           if p and os.path.basename(p.rstrip("/\\")) != "dazzlecmd-lib"])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    failed, tails = [], []
    for cwd, target in ((os.path.join(root, "lib"), "tests"),
                        (os.path.join(root, "tool"), "tests"),
                        (os.path.join(root, "wt"), "tests")):
        p = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
             "-x" if False else "--no-header", "--tb=no", "-rfE", target],
            cwd=cwd, env=env, capture_output=True, text=True, timeout=600)
        for line in p.stdout.splitlines():
            if line.startswith(("FAILED ", "ERROR ")):
                failed.append(line.split(" - ")[0].split(" ", 1)[1].split("::", 1)[-1])
        last = [ln for ln in p.stdout.splitlines() if ln.strip()]
        tails.append(last[-1] if last else "(no output) rc=%d %s" % (p.returncode, p.stderr[-200:]))
    return failed, tails


def main(argv):
    only = argv[1] if len(argv) > 1 else None
    root = tempfile.mkdtemp(prefix="dz-redgreen-p1-")
    lib, tool = build_copy(root)
    print("copy:", root)
    print("real source is not modified; the copy is left in place.\n")

    failed, tails = run_tests(root)
    print("GREEN baseline on the copy:", " | ".join(tails))
    if failed:
        print("baseline is not green -- stopping:", failed)
        return 2

    platform = "windows" if sys.platform == "win32" else "posix"
    unpinned = []
    for name, edits, note in MUTATIONS:
        if only and only not in name:
            continue
        if note in ("windows", "posix") and note != platform:
            print("SKIP  %-74s (runs on %s)" % (name, note))
            continue
        originals = {}
        problem = None
        for rel, old, new in edits:
            path = os.path.join(root, rel.replace("/", os.sep))
            text = originals.get(path)
            if text is None:
                with open(path, "r", encoding="utf-8", newline="") as fh:
                    text = fh.read()
                originals[path] = text
            current = getattr(main, "_cur", {}).get(path, text)
            count = current.count(old)
            if count != 1:
                problem = "anchor found %d times in %s: %r" % (count, rel, old[:60])
                break
            main._cur = getattr(main, "_cur", {})
            main._cur[path] = current.replace(old, new)
        if problem:
            print("BROKEN %-73s %s" % (name, problem))
            unpinned.append(name + " [anchor]")
            main._cur = {}
            continue
        for path, text in main._cur.items():
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
        try:
            failed, tails = run_tests(root)
        finally:
            for path, text in originals.items():
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(text)
            main._cur = {}
        if failed:
            print("RED   %-74s %d test(s): %s" % (name, len(failed), ", ".join(sorted(set(failed))[:3])
                                                + (" ..." if len(set(failed)) > 3 else "")))
        elif note == "second-layer":
            print("GREEN %-74s (expected: the first layer still holds; audited by the paired mutation)" % name)
        else:
            print("GREEN %-74s <-- NOT PINNED" % name)
            unpinned.append(name)

    failed, tails = run_tests(root)
    print("\nGREEN again on the restored copy:", " | ".join(tails))
    print("unpinned: %d" % len(unpinned))
    for u in unpinned:
        print("  -", u)
    return 1 if unpinned or failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
