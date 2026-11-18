"""Tear down a git worktree without deleting through its links.

``git worktree remove`` is not safe for a worktree that holds a junction or
symlink. Measured on git 2.52.0.windows.1 (2026-11-18): it deletes THROUGH a
``private/`` junction and empties the junction's target -- with ``--force``,
and also without it when ``private/`` is gitignored, which is the normal
setup. On 2026-11-17 that emptied two real repositories.

The safe teardown, tested the same day, is a fixed order of small steps, each
checked before the next:

    1. find every link inside the worktree (never entering one), and note
       what each points at and how many entries the target holds;
    2. remove the links -- only the links -- into the trash (recoverable:
       the manifest records each target);
    3. confirm every target still exists with the entries it had;
    4. move the worktree folder into the trash;
    5. confirm the folder is gone and the targets are still intact;
    6. ``git worktree prune`` in the main repository (it only drops the
       record of a worktree whose folder is missing).

Removing the links first matters: a folder moved to a trash store on another
volume is COPIED, and a tree copy enters junctions. With the links already
gone there is nothing to enter.

``plan_teardown`` decides and changes nothing; ``execute_teardown`` performs
it and stops at the first step whose check fails. Nothing here resolves a
path, and nothing here runs ``git worktree remove``.
"""

import os
import stat
import subprocess
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from dazzlecmd_lib.core.links import detect_link, LINK_JUNCTION, LINK_SYMLINK

from ._junctions import unregister_link


@dataclass
class WorktreeLink:
    path: str
    link_type: str                 # "junction" | "symlink"
    target: str
    target_exists: bool
    target_entries: Optional[int]  # entries in the target folder, if it is one


@dataclass
class TeardownPlan:
    worktree: str
    main_repo: Optional[str] = None
    worktree_name: Optional[str] = None      # its entry under .git/worktrees/
    links: List[WorktreeLink] = field(default_factory=list)
    refusals: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def refused(self) -> bool:
        return bool(self.refusals)

    def describe(self) -> List[str]:
        lines = [f"  worktree:  {self.worktree}"]
        if self.main_repo:
            lines.append(f"  main repo: {self.main_repo}")
        if not self.links:
            lines.append("  links inside it: none")
        for link in self.links:
            state = (f"{link.target_entries} entries" if link.target_entries
                     is not None else
                     ("exists" if link.target_exists else "MISSING"))
            lines.append(f"  link ({link.link_type}): {link.path}")
            lines.append(f"      -> {link.target}  [{state}; will NOT be touched]")
        lines.append("  steps:")
        n = 1
        if self.links:
            lines.append(f"    {n}. remove the {len(self.links)} link(s) only "
                         f"(to the trash; recoverable)")
            lines.append(f"    {n + 1}. check each link's target is intact")
            n += 2
        lines.append(f"    {n}. move the worktree folder into the trash")
        lines.append(f"    {n + 1}. check it is gone"
                     + (" and the targets are intact" if self.links else ""))
        lines.append(f"    {n + 2}. git worktree prune (in the main repo)")
        return lines


@dataclass
class TeardownResult:
    success: bool
    refused: bool = False
    steps: List[str] = field(default_factory=list)      # what was done, in order
    errors: List[str] = field(default_factory=list)
    batches: List[str] = field(default_factory=list)    # trash batch folder names
    plan: Optional[TeardownPlan] = None


def _is_reparse(st) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(
        (getattr(st, "st_file_attributes", 0) or 0) & 0x400)


def _read_worktree_pointer(worktree: str):
    """(main repo, worktree name) from a linked worktree's ``.git`` FILE.

    The file holds ``gitdir: <main>/.git/worktrees/<name>``. Returns
    (None, None, reason) when this is not a linked worktree.
    """
    dot_git = os.path.join(worktree, ".git")
    try:
        st = os.lstat(dot_git)
    except OSError:
        return None, None, "it has no .git entry, so it is not a git worktree"
    if stat.S_ISDIR(st.st_mode) and not _is_reparse(st):
        return None, None, ("its .git is a folder: this is a repository's "
                            "main checkout, not a linked worktree")
    try:
        with open(dot_git, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read().strip()
    except OSError as exc:
        return None, None, f"its .git file cannot be read ({exc})"
    if not text.lower().startswith("gitdir:"):
        return None, None, "its .git file does not name a gitdir"
    gitdir = text[len("gitdir:"):].strip().replace("/", os.sep)
    if not os.path.isabs(gitdir):
        gitdir = os.path.normpath(os.path.join(worktree, gitdir))
    parts = gitdir.rstrip("\\/").split(os.sep)
    # .../<main>/.git/worktrees/<name>
    if len(parts) < 4 or parts[-2].lower() != "worktrees" or parts[-3] != ".git":
        return None, None, ("its .git file points at "
                            f"{gitdir}, which is not a worktree entry "
                            "(a submodule, perhaps)")
    main_repo = os.sep.join(parts[:-3])
    return main_repo, parts[-1], None


def find_links(folder: str) -> List[WorktreeLink]:
    """Every junction and symlink under ``folder``, at any depth, found
    without entering any of them. Deepest first, so removing them in order
    never leaves a link inside a folder that is about to change."""
    found: List[WorktreeLink] = []
    stack = [folder]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if _is_reparse(st):
                try:
                    info = detect_link(entry.path)
                except Exception:
                    info = None
                target = (info.target if info is not None else "") or ""
                if not target:
                    try:
                        target = os.readlink(entry.path)
                    except (OSError, NotImplementedError):
                        target = ""
                if target.startswith("\\\\?\\"):
                    target = target[4:]
                kind = ("junction" if info is not None
                        and info.link_type == LINK_JUNCTION else "symlink")
                exists = bool(target) and os.path.lexists(target)
                count = None
                if exists:
                    try:
                        t_st = os.lstat(target)
                        if stat.S_ISDIR(t_st.st_mode) and not _is_reparse(t_st):
                            count = len(os.listdir(target))
                    except OSError:
                        count = None
                found.append(WorktreeLink(entry.path, kind, target, exists, count))
            elif stat.S_ISDIR(st.st_mode):
                stack.append(entry.path)
    found.sort(key=lambda l: l.path.count(os.sep), reverse=True)
    return found


def plan_teardown(worktree: str) -> TeardownPlan:
    """Decide the teardown of ``worktree``. Changes nothing."""
    worktree = os.path.abspath(worktree)
    plan = TeardownPlan(worktree=worktree)
    try:
        st = os.lstat(worktree)
    except OSError:
        plan.refusals.append(f"{worktree} does not exist")
        return plan
    if _is_reparse(st):
        plan.refusals.append(
            f"{worktree} is itself a link; name the real worktree folder")
        return plan
    if not stat.S_ISDIR(st.st_mode):
        plan.refusals.append(f"{worktree} is not a folder")
        return plan

    main_repo, name, why_not = _read_worktree_pointer(worktree)
    if why_not:
        plan.refusals.append(f"{worktree}: {why_not}")
        return plan
    plan.main_repo, plan.worktree_name = main_repo, name
    if not os.path.isdir(os.path.join(main_repo, ".git")):
        plan.refusals.append(
            f"the main repository {main_repo} named by this worktree was not "
            f"found, so the worktree's record cannot be pruned")
        return plan

    plan.links = find_links(worktree)
    for link in plan.links:
        if not link.target_exists:
            plan.warnings.append(
                f"the link {link.path} points at {link.target or '(unknown)'}, "
                f"which does not exist; the link will still be removed")
    return plan


def _git_prune(main_repo: str) -> subprocess.CompletedProcess:
    """``git worktree prune``: drops the record of worktrees whose folder is
    missing. It deletes no working files."""
    return subprocess.run(
        ["git", "-C", main_repo, "worktree", "prune"],
        capture_output=True, text=True)


def execute_teardown(plan: TeardownPlan, store, *,
                     registry_path: Optional[str] = None,
                     prune: Callable[[str], object] = _git_prune,
                     scopes=()) -> TeardownResult:
    """Perform a teardown plan. Stops at the first step whose check fails.

    ``store`` is a ``TrashStore``. ``prune`` is the function that runs
    ``git worktree prune`` (a parameter so tests can observe or replace it).
    """
    result = TeardownResult(success=False, plan=plan)
    if plan.refused:
        result.refused = True
        result.errors = list(plan.refusals)
        return result

    def targets_intact(after: str) -> bool:
        ok = True
        for link in plan.links:
            if not link.target_exists:
                continue
            if not os.path.lexists(link.target):
                result.errors.append(
                    f"{after}: the target {link.target} of the link "
                    f"{link.path} no longer exists")
                ok = False
                continue
            if link.target_entries is not None:
                try:
                    now = len(os.listdir(link.target))
                except OSError as exc:
                    result.errors.append(
                        f"{after}: the target {link.target} cannot be "
                        f"listed ({exc})")
                    ok = False
                    continue
                if now != link.target_entries:
                    result.errors.append(
                        f"{after}: the target {link.target} had "
                        f"{link.target_entries} entries and has {now}")
                    ok = False
        return ok

    # 1-2. the links, and only the links. One call per link: two links may
    # share a name (private/ in two subfolders), and one batch holds each
    # name once.
    for link in plan.links:
        r = store.trash([link.path], scopes=scopes)
        if not r.success:
            result.errors.append(
                f"could not remove the link {link.path}: "
                + "; ".join(r.errors + r.postcondition_failures))
            result.refused = bool(getattr(r, "refused", False))
            return result
        if r.folder_name:
            result.batches.append(r.folder_name)
        result.steps.append(f"removed the link {link.path} "
                            f"(target untouched: {link.target})")
        if os.path.lexists(link.path):
            result.errors.append(f"the link {link.path} is still there")
            return result
    if plan.links:
        if not targets_intact("after removing the links"):
            return result
        result.steps.append("checked: every link target is intact")

    # 3. no link may be left inside before the folder moves
    left = find_links(plan.worktree)
    if left:
        result.errors.append(
            "links are still inside the worktree, so the folder was not "
            "moved: " + ", ".join(l.path for l in left))
        return result

    # 4-5. the folder itself
    r = store.trash([plan.worktree], scopes=scopes)
    if not r.success:
        result.errors.append(
            f"could not move the worktree folder to the trash: "
            + "; ".join(r.errors + r.postcondition_failures))
        result.refused = bool(getattr(r, "refused", False))
        return result
    if r.folder_name:
        result.batches.append(r.folder_name)
    result.steps.append(f"moved {plan.worktree} into the trash "
                        f"({r.folder_name})")
    if os.path.lexists(plan.worktree):
        result.errors.append(f"{plan.worktree} is still there")
        return result
    if not targets_intact("after moving the worktree"):
        return result
    result.steps.append("checked: the worktree is gone"
                        + (" and every link target is intact"
                           if plan.links else ""))

    # 6. git's bookkeeping
    try:
        done = prune(plan.main_repo)
        rc = getattr(done, "returncode", 0)
        if rc not in (0, None):
            result.errors.append(
                "git worktree prune failed (exit %s): %s"
                % (rc, (getattr(done, "stderr", "") or "").strip()))
            return result
    except OSError as exc:
        result.errors.append(f"git worktree prune could not run: {exc}")
        return result
    result.steps.append(f"git worktree prune in {plan.main_repo}")

    if registry_path:
        for link in plan.links:
            try:
                unregister_link(link.path, registry_path)
            except OSError:
                pass

    result.success = True
    return result
